"""Turn what docker prints into the types in `models`. Pure functions, no I/O.

Each function reads the *shape* of the output (JSON keys, the position of a size
among words) and not its wording, so it keeps working when docker changes a label.
Anything that is missing or unreadable becomes "unknown" (`None`), never a guess.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from docker_tidy.models import ContainerInfo, ImageInfo, NetworkInfo, VolumeInfo

_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "pb": 1000**5,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
    "pib": 1024**5,
}
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]+)\s*$")
_WHEN = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?\s*(Z|[+-]\d{4})?")


class ParseError(ValueError):
    """The output was not in a shape this program understands."""


def parse_size(text: str) -> int | None:
    """Read docker's human sizes ("78.1MB", "0B", "1.2GiB"). "N/A" and junk are None."""
    match = _SIZE.match(text)
    if match is None:
        return None
    factor = _UNITS.get(match.group(2).lower())
    if factor is None:
        return None
    return round(float(match.group(1)) * factor)


def parse_when(text: str) -> datetime | None:
    """Read the two date styles docker uses.

    `2024-03-05 09:30:00 +0000 UTC` (list commands) and `2024-03-05T09:30:00.123Z`
    (inspect). A missing offset is taken as UTC.
    """
    match = _WHEN.search(text)
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(g) for g in match.groups()[:6])
    offset = match.group(7)
    zone = UTC
    if offset and offset != "Z":
        sign = -1 if offset[0] == "-" else 1
        delta = timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5]))
        zone = timezone(sign * delta)
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=zone)
    except ValueError:
        return None


def normalise_id(value: str) -> str:
    """Image ids appear with and without a `sha256:` prefix; compare them without it."""
    return value.removeprefix("sha256:")


def _rows(text: str) -> list[dict[str, Any]]:
    """Read `--format json` output: one object per line (or, in older clients, a list)."""
    stripped = text.strip()
    if not stripped:
        return []
    try:
        if stripped.startswith("["):
            loaded: Any = json.loads(stripped)
            items = loaded if isinstance(loaded, list) else [loaded]
        else:
            items = [json.loads(line) for line in stripped.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        msg = f"docker printed something that is not JSON: {exc}"
        raise ParseError(msg) from exc
    rows = [item for item in items if isinstance(item, dict)]
    if len(rows) != len(items):
        msg = "docker printed JSON that is not a list of objects"
        raise ParseError(msg)
    return rows


def _text(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sequence(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def parse_images(text: str) -> list[ImageInfo]:
    """`docker image ls --no-trunc --format json`."""
    return [
        ImageInfo(
            id=normalise_id(_text(row.get("ID"))),
            repository=_text(row.get("Repository"), "<none>"),
            tag=_text(row.get("Tag"), "<none>"),
            size=parse_size(_text(row.get("Size"))),
            created=parse_when(_text(row.get("CreatedAt"))),
        )
        for row in _rows(text)
    ]


def parse_volumes(text: str) -> list[VolumeInfo]:
    """`docker volume ls --format json`."""
    return [
        VolumeInfo(name=_text(row.get("Name")), driver=_text(row.get("Driver")))
        for row in _rows(text)
        if _text(row.get("Name"))
    ]


def parse_networks(text: str) -> list[NetworkInfo]:
    """`docker network ls --no-trunc --format json`."""
    return [
        NetworkInfo(
            id=_text(row.get("ID")),
            name=_text(row.get("Name")),
            driver=_text(row.get("Driver")),
            created=parse_when(_text(row.get("CreatedAt"))),
        )
        for row in _rows(text)
        if _text(row.get("ID"))
    ]


def parse_containers(text: str) -> list[ContainerInfo]:
    """`docker container inspect --size <ids>`, which prints one JSON list."""
    containers: list[ContainerInfo] = []
    for row in _rows(text):
        state = _mapping(row.get("State"))
        config = _mapping(row.get("Config"))
        host = _mapping(row.get("HostConfig"))
        networks = set(_mapping(_mapping(row.get("NetworkSettings")).get("Networks")))
        mode = _text(host.get("NetworkMode"))
        if mode == "default":
            networks.add("bridge")
        elif mode and not mode.startswith("container:"):
            networks.add(mode)
        volumes = tuple(
            sorted(
                _text(_mapping(mount).get("Name"))
                for mount in _sequence(row.get("Mounts"))
                if _text(_mapping(mount).get("Type")) == "volume"
                and _text(_mapping(mount).get("Name"))
            )
        )
        size_rw = row.get("SizeRw")
        containers.append(
            ContainerInfo(
                id=_text(row.get("Id")),
                name=_text(row.get("Name")).removeprefix("/"),
                image_id=normalise_id(_text(row.get("Image"))),
                image_name=_text(config.get("Image")),
                state=_text(state.get("Status"), "unknown"),
                size_rw=size_rw if isinstance(size_rw, int) else None,
                created=parse_when(_text(row.get("Created"))),
                volumes=volumes,
                networks=tuple(sorted(networks)),
            )
        )
    return containers


def parse_volume_sizes(text: str) -> dict[str, int]:
    """Volume sizes from the plain text of `docker system df -v`.

    `--format` is ignored by that command, and `docker volume ls` always prints "N/A"
    for the size, so this table is the only place the CLI shows per-volume sizes. It is
    found by its header (`VOLUME NAME ... SIZE`) and read as: the name first, the size
    last. A line that does not look like that is skipped.
    """
    sizes: dict[str, int] = {}
    in_table = False
    for line in text.splitlines():
        words = line.split()
        if not in_table:
            in_table = len(words) >= 2 and words[0] == "VOLUME" and words[1] == "NAME"
            continue
        if not words:
            break
        size = parse_size(words[-1])
        if size is not None and len(words) >= 2:
            sizes[words[0]] = size
    return sizes
