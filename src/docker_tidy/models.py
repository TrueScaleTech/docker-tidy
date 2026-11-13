"""Plain data types. Nothing in this module reads files or talks to Docker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Kind(StrEnum):
    """The four kinds of thing docker-tidy can list and remove."""

    CONTAINER = "container"
    IMAGE = "image"
    VOLUME = "volume"
    NETWORK = "network"


# The order in which things are removed. A stopped container has to go before the
# image it was created from, and a network or volume can only go once nothing is
# attached to it.
KIND_ORDER: tuple[Kind, ...] = (Kind.CONTAINER, Kind.IMAGE, Kind.VOLUME, Kind.NETWORK)


class Usage(StrEnum):
    """Whether a resource may be offered for removal."""

    UNUSED = "unused"
    IN_USE = "in use"
    PROTECTED = "protected"


@dataclass(frozen=True, slots=True)
class ContainerInfo:
    """What `docker container inspect` says about one container."""

    id: str
    name: str
    image_id: str
    image_name: str
    state: str
    size_rw: int | None
    created: datetime | None
    volumes: tuple[str, ...]
    networks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImageInfo:
    """One row of `docker image ls`: an image under one repository and tag."""

    id: str
    repository: str
    tag: str
    size: int | None
    created: datetime | None


@dataclass(frozen=True, slots=True)
class VolumeInfo:
    name: str
    driver: str


@dataclass(frozen=True, slots=True)
class NetworkInfo:
    id: str
    name: str
    driver: str
    created: datetime | None


@dataclass(frozen=True, slots=True)
class Resource:
    """One thing that could be removed, with the reason it can or cannot be."""

    kind: Kind
    key: str
    name: str
    size: int | None
    created: datetime | None
    usage: Usage
    reason: str
    # Each entry is the argument list of one `docker` command, without the word
    # "docker". An image with two tags needs two `rmi` commands.
    remove_args: tuple[tuple[str, ...], ...]
    preselected: bool = False
    note: str = ""

    @property
    def removable(self) -> bool:
        return self.usage is Usage.UNUSED


@dataclass(frozen=True, slots=True)
class Inventory:
    resources: tuple[Resource, ...]

    def of_kind(self, kind: Kind) -> tuple[Resource, ...]:
        return tuple(r for r in self.resources if r.kind is kind)

    def reclaimable(self, kind: Kind | None = None) -> int:
        """Bytes docker reports for unused resources. Unknown sizes count as zero.

        This is an upper estimate for images: layers shared with an image that stays
        are not freed.
        """
        return sum(
            r.size or 0 for r in self.resources if r.removable and (kind is None or r.kind is kind)
        )
