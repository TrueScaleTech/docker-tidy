"""Carry out a cleanup plan, one removal at a time."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from docker_tidy.analysis import PlannedCommand
from docker_tidy.collect import collect_inventory
from docker_tidy.runner import DockerError, Runner


class Status(StrEnum):
    REMOVED = "removed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class Outcome:
    command: PlannedCommand
    status: Status
    message: str = ""


def run_cleanup(plan: Sequence[PlannedCommand], runner: Runner) -> list[Outcome]:
    """Run each command of `plan`, after reading docker's state again.

    The plan was made from a scan that may be minutes old. Right before running
    anything, docker is asked again, and a resource that has since been removed or put
    to use is skipped instead of attempted. Docker itself also refuses to remove
    anything in use without `--force`, which this program never passes, so a race
    between the scan and the removal can fail a command but cannot damage a service.

    A command that fails does not stop the others.
    """
    fresh = {(r.kind, r.key): r for r in collect_inventory(runner).resources}
    outcomes: list[Outcome] = []
    for command in plan:
        current = fresh.get((command.resource.kind, command.resource.key))
        if current is None:
            outcomes.append(Outcome(command, Status.SKIPPED, "it no longer exists"))
        elif not current.removable:
            outcomes.append(Outcome(command, Status.SKIPPED, f"it is now {current.usage.value}"))
        else:
            try:
                runner.run(command.args)
            except DockerError as exc:
                outcomes.append(Outcome(command, Status.FAILED, str(exc)))
            else:
                outcomes.append(Outcome(command, Status.REMOVED))
    return outcomes
