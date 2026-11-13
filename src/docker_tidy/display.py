"""Words and numbers for people. Shared by the command line and the terminal screen."""

from __future__ import annotations

from datetime import datetime

from docker_tidy.models import Kind

_UNITS = ("B", "kB", "MB", "GB", "TB")

KIND_TITLES = {
    Kind.CONTAINER: "Containers",
    Kind.IMAGE: "Images",
    Kind.VOLUME: "Volumes",
    Kind.NETWORK: "Networks",
}


def format_size(size: int | None) -> str:
    """Sizes the way docker writes them (powers of 1000). Unknown is a question mark."""
    if size is None:
        return "?"
    value = float(size)
    for unit in _UNITS[:-1]:
        if value < 1000:
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} {_UNITS[-1]}"


def format_age(created: datetime | None, now: datetime) -> str:
    """A short age: 5m, 3h, 12d, 8mo, 2y. Unknown is a question mark."""
    if created is None:
        return "?"
    seconds = max(0, int((now - created).total_seconds()))
    days = seconds // 86_400
    if seconds < 3_600:
        return f"{seconds // 60}m"
    if seconds < 86_400:
        return f"{seconds // 3_600}h"
    if days < 30:
        return f"{days}d"
    if days < 365:
        return f"{days // 30}mo"
    return f"{days // 365}y"
