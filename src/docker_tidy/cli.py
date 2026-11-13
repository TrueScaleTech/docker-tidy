"""The command line: `report` and `clean`.

Exit codes: 0 everything went as asked; 1 docker could not be used or a removal failed;
2 the request itself was wrong (an unknown name, something in use).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TextIO

from docker_tidy import __version__
from docker_tidy.analysis import NotRemovableError, plan_cleanup
from docker_tidy.cleaner import Status, run_cleanup
from docker_tidy.collect import collect_inventory
from docker_tidy.display import KIND_TITLES, format_age, format_size
from docker_tidy.models import KIND_ORDER, Inventory, Kind, Resource
from docker_tidy.parse import ParseError
from docker_tidy.runner import DockerError, Runner, SubprocessRunner

_KIND_NAMES = [k.value for k in KIND_ORDER]


class _UsageError(Exception):
    """The request names something that cannot be done. Reported with exit code 2."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docker-tidy",
        description="See what Docker keeps that nothing uses, and clean it up safely.",
    )
    parser.add_argument("--version", action="version", version=f"docker-tidy {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    report = commands.add_parser("report", help="list what is unused, with sizes")
    report.add_argument(
        "--all", action="store_true", help="also list what is in use or protected, and why"
    )
    report.add_argument(
        "--kind", action="append", choices=_KIND_NAMES, help="only this kind (repeatable)"
    )
    report.add_argument("--json", action="store_true", help="machine-readable output")

    clean = commands.add_parser(
        "clean",
        help="remove unused things (shows what it would do unless --yes is given)",
        description=(
            "Without --kind or --name, selects stopped containers and untagged images. "
            "Volumes, networks and tagged images are only removed when named here."
        ),
    )
    clean.add_argument(
        "--kind",
        action="append",
        choices=_KIND_NAMES,
        help="select every unused resource of this kind (repeatable)",
    )
    clean.add_argument(
        "--name",
        action="append",
        help="select one unused resource by name, tag or id (repeatable)",
    )
    mode = clean.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="only show the commands (default)")
    mode.add_argument("--yes", action="store_true", help="run the commands")
    return parser


# --- report --------------------------------------------------------------------------


def _row(r: Resource, now: datetime) -> list[str]:
    why = r.reason if not r.note else f"{r.reason} - {r.note}"
    return [r.name, format_size(r.size), format_age(r.created, now), r.usage.value, why]


def _table(rows: list[list[str]], header: list[str]) -> list[str]:
    widths = [max(len(line[i]) for line in [header, *rows]) for i in range(len(header))]

    def fmt(cells: list[str]) -> str:
        return "  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)).rstrip()

    return [fmt(header), *(fmt(r) for r in rows)]


def _report_text(inv: Inventory, kinds: Sequence[Kind], show_all: bool, now: datetime) -> str:
    lines: list[str] = []
    for kind in kinds:
        items = [r for r in inv.of_kind(kind) if show_all or r.removable]
        unused = sum(1 for r in inv.of_kind(kind) if r.removable)
        lines.append(f"{KIND_TITLES[kind]} ({unused} unused)")
        if items:
            table = _table([_row(r, now) for r in items], ["NAME", "SIZE", "AGE", "STATE", "WHY"])
            lines.extend(f"  {line}" for line in table)
        else:
            lines.append("  nothing to clean")
        lines.append("")
    total = sum(inv.reclaimable(kind) for kind in kinds)
    lines.append(f"Reclaimable: {format_size(total)}")
    if Kind.IMAGE in kinds:
        lines.append("(An image's full size is counted; layers shared with images you keep stay.)")
    return "\n".join(lines)


def _report_json(inv: Inventory, kinds: Sequence[Kind], show_all: bool) -> str:
    resources = [
        {
            "kind": r.kind.value,
            "id": r.key,
            "name": r.name,
            "size_bytes": r.size,
            "created": r.created.isoformat() if r.created else None,
            "usage": r.usage.value,
            "reason": r.reason,
            "note": r.note,
        }
        for r in inv.resources
        if r.kind in kinds and (show_all or r.removable)
    ]
    body = {
        "version": __version__,
        "reclaimable_bytes": sum(inv.reclaimable(kind) for kind in kinds),
        "resources": resources,
    }
    return json.dumps(body, indent=2)


def _cmd_report(args: argparse.Namespace, runner: Runner, out: TextIO) -> int:
    inv = collect_inventory(runner)
    kinds = [Kind(k) for k in args.kind] if args.kind else list(KIND_ORDER)
    kinds = [k for k in KIND_ORDER if k in kinds]
    if args.json:
        print(_report_json(inv, kinds, args.all), file=out)
    else:
        print(_report_text(inv, kinds, args.all, datetime.now(UTC)), file=out)
    return 0


# --- clean ---------------------------------------------------------------------------


def _matches(r: Resource, wanted: str) -> bool:
    tags = [part.strip() for part in r.name.split(",")]
    return wanted in (r.name, r.key, *tags) or (len(wanted) >= 6 and r.key.startswith(wanted))


def _select(
    inv: Inventory, kinds: Sequence[str] | None, names: Sequence[str] | None
) -> list[Resource]:
    if not kinds and not names:
        return [r for r in inv.resources if r.removable and r.preselected]

    chosen: dict[tuple[Kind, str], Resource] = {}
    for r in inv.resources:
        if r.removable and kinds and r.kind.value in kinds:
            chosen[(r.kind, r.key)] = r
    for wanted in names or []:
        found = [r for r in inv.resources if _matches(r, wanted)]
        if not found:
            msg = f"No container, image, volume or network matches '{wanted}'."
            raise _UsageError(msg)
        for r in found:
            if not r.removable:
                msg = f"Will not remove {r.kind.value} '{r.name}': {r.reason}."
                raise _UsageError(msg)
            chosen[(r.kind, r.key)] = r
    return list(chosen.values())


def _cmd_clean(args: argparse.Namespace, runner: Runner, out: TextIO) -> int:
    inv = collect_inventory(runner)
    chosen = _select(inv, args.kind, args.name)
    try:
        plan = plan_cleanup(chosen)
    except NotRemovableError as exc:  # pragma: no cover - _select already filters these
        raise _UsageError(str(exc)) from exc

    if not plan:
        print("Nothing to remove.", file=out)
        return 0

    if not args.yes:
        print("Dry run. These commands would be run:", file=out)
        for step in plan:
            print(f"  {step.text}", file=out)
        print("Add --yes to run them.", file=out)
        return 0

    failures = 0
    for outcome in run_cleanup(plan, runner):
        detail = f" ({outcome.message})" if outcome.message else ""
        print(f"{outcome.status.value:<8}{outcome.command.text}{detail}", file=out)
        failures += outcome.status is Status.FAILED
    return 1 if failures else 0


# --- entry point ---------------------------------------------------------------------


def _default_screen(runner: Runner) -> int:  # pragma: no cover - needs a real terminal
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(
            "docker-tidy: the interactive screen needs a terminal. "
            "Use 'docker-tidy report' in scripts.",
            file=sys.stderr,
        )
        return 2
    from docker_tidy.tui.app import run  # imported late: report and clean never need Textual

    return run(runner)


def main(
    argv: Sequence[str] | None = None,
    runner: Runner | None = None,
    out: TextIO | None = None,
    screen: Callable[[Runner], int] = _default_screen,
) -> int:
    """Run the command line. `runner`, `out` and `screen` exist so tests can replace them."""
    parser = build_parser()
    args = parser.parse_args(argv)
    out = out or sys.stdout
    runner = runner or SubprocessRunner()
    if args.command is None:
        return screen(runner)
    try:
        if args.command == "report":
            return _cmd_report(args, runner, out)
        return _cmd_clean(args, runner, out)
    except _UsageError as exc:
        print(f"docker-tidy: {exc}", file=sys.stderr)
        return 2
    except (DockerError, ParseError) as exc:
        print(f"docker-tidy: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
