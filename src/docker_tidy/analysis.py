"""Decide what is unused, what is protected, and which commands would remove it.

Pure functions over the records in `models`. This is the part of the program that must
not be wrong: it is what keeps a running service's image, volume or network off the
list of things offered for removal.
"""

from __future__ import annotations

import re
import shlex
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from docker_tidy.models import (
    KIND_ORDER,
    ContainerInfo,
    ImageInfo,
    Inventory,
    Kind,
    NetworkInfo,
    Resource,
    Usage,
    VolumeInfo,
)

# Containers in these states are doing something; anything else has finished or never
# started. "removing" is included so a container docker is already deleting is left alone.
STOPPED_STATES = frozenset({"created", "exited", "dead"})

# Networks docker creates itself, plus the ones swarm adds. They cannot be removed anyway.
BUILT_IN_NETWORKS = frozenset({"bridge", "host", "none", "ingress", "docker_gwbridge"})

_ANONYMOUS_VOLUME = re.compile(r"^[0-9a-f]{64}$")
_NO_VALUE = "<none>"


def _names(names: Iterable[str], limit: int = 3) -> str:
    ordered = sorted(names)
    shown = ", ".join(ordered[:limit])
    return shown if len(ordered) <= limit else f"{shown} and {len(ordered) - limit} more"


def _containers(containers: Iterable[ContainerInfo]) -> list[Resource]:
    found: list[Resource] = []
    for c in containers:
        stopped = c.state in STOPPED_STATES
        found.append(
            Resource(
                kind=Kind.CONTAINER,
                key=c.id,
                name=c.name or c.id[:12],
                size=c.size_rw,
                created=c.created,
                usage=Usage.UNUSED if stopped else Usage.IN_USE,
                reason=f"stopped ({c.state})" if stopped else f"container is {c.state}",
                remove_args=(("rm", c.id),),
                preselected=stopped,
                note=f"image {c.image_name}" if c.image_name else "",
            )
        )
    return found


def _images(images: Iterable[ImageInfo], containers: list[ContainerInfo]) -> list[Resource]:
    rows: dict[str, list[ImageInfo]] = defaultdict(list)
    for image in images:
        rows[image.id].append(image)

    live: dict[str, list[str]] = defaultdict(list)
    stopped: dict[str, list[str]] = defaultdict(list)
    for c in containers:
        (stopped if c.state in STOPPED_STATES else live)[c.image_id].append(c.name or c.id[:12])

    found: list[Resource] = []
    for image_id, group in rows.items():
        tagged = sorted(
            f"{i.repository}:{i.tag}" for i in group if _NO_VALUE not in (i.repository, i.tag)
        )
        first = group[0]
        if live[image_id]:
            usage, reason = Usage.IN_USE, f"used by running container {_names(live[image_id])}"
        elif stopped[image_id]:
            usage = Usage.IN_USE
            reason = f"used by stopped container {_names(stopped[image_id])}; remove that first"
        else:
            usage, reason = Usage.UNUSED, "no container uses it"
        # An image that carries tags is removed by naming each tag; one with none is
        # removed by its id. Naming only the id of a multi-tag image would be refused.
        remove = tuple(("rmi", ref) for ref in tagged) or (("rmi", image_id),)
        found.append(
            Resource(
                kind=Kind.IMAGE,
                key=image_id,
                name=", ".join(tagged) if tagged else f"<untagged> {image_id[:12]}",
                size=first.size,
                created=first.created,
                usage=usage,
                reason=reason,
                remove_args=remove,
                preselected=usage is Usage.UNUSED and not tagged,
                note="" if tagged else "dangling: nothing refers to it by name",
            )
        )
    return found


def _volumes(
    volumes: Iterable[VolumeInfo],
    containers: list[ContainerInfo],
    sizes: Mapping[str, int],
) -> list[Resource]:
    mounted: dict[str, list[str]] = defaultdict(list)
    for c in containers:
        for volume in c.volumes:
            mounted[volume].append(c.name or c.id[:12])

    found: list[Resource] = []
    for v in volumes:
        users = mounted[v.name]
        anonymous = bool(_ANONYMOUS_VOLUME.match(v.name))
        found.append(
            Resource(
                kind=Kind.VOLUME,
                key=v.name,
                name=f"{v.name[:12]} (anonymous)" if anonymous else v.name,
                size=sizes.get(v.name),
                created=None,
                usage=Usage.IN_USE if users else Usage.UNUSED,
                reason=f"mounted by {_names(users)}" if users else "no container mounts it",
                remove_args=(("volume", "rm", v.name),),
                # Removing a volume deletes its data for good, so it is never ticked for you.
                preselected=False,
                note="" if users else "removing it deletes its data",
            )
        )
    return found


def _networks(networks: Iterable[NetworkInfo], containers: list[ContainerInfo]) -> list[Resource]:
    attached: dict[str, list[str]] = defaultdict(list)
    for c in containers:
        for network in c.networks:
            attached[network].append(c.name or c.id[:12])

    found: list[Resource] = []
    for n in networks:
        users = attached[n.name]
        if n.name in BUILT_IN_NETWORKS:
            usage, reason = Usage.PROTECTED, "built-in network"
        elif users:
            usage, reason = Usage.IN_USE, f"used by {_names(users)}"
        else:
            usage, reason = Usage.UNUSED, "no container is connected to it"
        found.append(
            Resource(
                kind=Kind.NETWORK,
                key=n.id,
                name=n.name,
                size=None,
                created=n.created,
                usage=usage,
                reason=reason,
                remove_args=(("network", "rm", n.id),),
            )
        )
    return found


def build_inventory(
    containers: Iterable[ContainerInfo],
    images: Iterable[ImageInfo],
    volumes: Iterable[VolumeInfo],
    networks: Iterable[NetworkInfo],
    volume_sizes: Mapping[str, int],
) -> Inventory:
    """Combine what docker reported into one list, unused things first, biggest first."""
    known = list(containers)
    resources = [
        *_containers(known),
        *_images(images, known),
        *_volumes(volumes, known, volume_sizes),
        *_networks(networks, known),
    ]
    usage_rank = {Usage.UNUSED: 0, Usage.IN_USE: 1, Usage.PROTECTED: 2}
    resources.sort(
        key=lambda r: (KIND_ORDER.index(r.kind), usage_rank[r.usage], -(r.size or 0), r.name)
    )
    return Inventory(tuple(resources))


# --- the cleanup plan -----------------------------------------------------------------


class NotRemovableError(ValueError):
    """Something that is in use or protected was put into a cleanup plan."""


@dataclass(frozen=True, slots=True)
class PlannedCommand:
    resource: Resource
    args: tuple[str, ...]

    @property
    def text(self) -> str:
        """The command as it would be typed, for showing to a person."""
        return shlex.join(("docker", *self.args))


def plan_cleanup(resources: Iterable[Resource]) -> tuple[PlannedCommand, ...]:
    """The exact commands that would remove `resources`, in a safe order.

    Refuses outright if any resource is in use or protected: the check is repeated here
    so that no screen or script can build a plan that touches something live.
    """
    chosen = list(resources)
    blocked = [r for r in chosen if not r.removable]
    if blocked:
        names = ", ".join(f"{r.kind.value} {r.name} ({r.usage.value})" for r in blocked)
        msg = f"Refusing to remove: {names}"
        raise NotRemovableError(msg)
    chosen.sort(key=lambda r: KIND_ORDER.index(r.kind))
    return tuple(PlannedCommand(r, args) for r in chosen for args in r.remove_args)
