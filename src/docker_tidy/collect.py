"""Ask docker what it holds and build the inventory. The only place that sequences calls."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from docker_tidy.analysis import build_inventory
from docker_tidy.models import Inventory
from docker_tidy.parse import (
    parse_containers,
    parse_images,
    parse_networks,
    parse_volume_sizes,
    parse_volumes,
)
from docker_tidy.runner import DockerError, Runner

# `docker container inspect` takes ids as arguments; a long host would otherwise
# overflow the command line limit on Windows.
_INSPECT_BATCH = 50


def _batches(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def collect_inventory(runner: Runner) -> Inventory:
    """Read containers, images, volumes and networks and decide what is unused.

    Raises `DockerError` if docker is missing or its daemon is not reachable, and
    `ParseError` if docker prints something this program cannot read.
    """
    # Cheapest call that needs the daemon: fail early, with docker's own explanation.
    runner.run(["version", "--format", "{{.Server.Version}}"])

    ids = runner.run(["container", "ls", "--all", "--quiet", "--no-trunc"]).split()
    containers = [
        info
        for batch in _batches(ids, _INSPECT_BATCH)
        for info in parse_containers(runner.run(["container", "inspect", "--size", *batch]))
    ]
    images = parse_images(runner.run(["image", "ls", "--no-trunc", "--format", "json"]))
    volumes = parse_volumes(runner.run(["volume", "ls", "--format", "json"]))
    networks = parse_networks(runner.run(["network", "ls", "--no-trunc", "--format", "json"]))

    try:
        sizes = parse_volume_sizes(runner.run(["system", "df", "-v"]))
    except DockerError:
        # Sizes are a convenience. Without them volumes are listed with an unknown size.
        sizes = {}

    return build_inventory(containers, images, volumes, networks, sizes)
