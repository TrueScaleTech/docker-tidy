"""A stand-in for the docker command, holding an invented host in memory.

It prints what docker prints (JSON lines, the `system df -v` table) and, like docker,
refuses to remove a running container, an image a container uses, a mounted volume or a
connected network. So the same fake serves the unit tests and the end-to-end ones.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace

import scenario
from docker_tidy.models import ContainerInfo, ImageInfo, NetworkInfo, VolumeInfo
from docker_tidy.runner import DockerError

_WHEN = "2024-03-05 09:30:00 +0000 UTC"


def human(size: int | None) -> str:
    """Format like docker does, using the largest unit that divides the size exactly."""
    if size is None:
        return "N/A"
    for unit, factor in (("GB", 10**9), ("MB", 10**6), ("kB", 10**3)):
        if size >= factor and size % factor == 0:
            return f"{size // factor}{unit}"
    return f"{size}B"


class FakeDocker:
    def __init__(
        self,
        *,
        containers: list[ContainerInfo] | None = None,
        images: list[ImageInfo] | None = None,
        volumes: list[VolumeInfo] | None = None,
        networks: list[NetworkInfo] | None = None,
        volume_sizes: dict[str, int] | None = None,
    ) -> None:
        self.containers = containers if containers is not None else scenario.containers()
        self.images = images if images is not None else scenario.images()
        self.volumes = volumes if volumes is not None else scenario.volumes()
        self.networks = networks if networks is not None else scenario.networks()
        self.volume_sizes = (
            volume_sizes if volume_sizes is not None else dict(scenario.VOLUME_SIZES)
        )
        self.down: str | None = None  # a message makes every call fail like a dead daemon
        self.df_fails = False
        self.calls: list[tuple[str, ...]] = []

    # -- the Runner protocol -----------------------------------------------------------

    def run(self, args: Sequence[str]) -> str:
        call = tuple(args)
        self.calls.append(call)
        if self.down is not None:
            raise DockerError(self.down)
        match call:
            case ("version", *_):
                return "27.0.0\n"
            case ("container", "ls", *_):
                return "".join(f"{c.id}\n" for c in self.containers)
            case ("container", "inspect", "--size", *ids):
                return json.dumps([self._inspect(c) for c in self.containers if c.id in ids])
            case ("image", "ls", *_):
                return "".join(json.dumps(self._image_row(i)) + "\n" for i in self.images)
            case ("volume", "ls", *_):
                return "".join(
                    json.dumps({"Name": v.name, "Driver": v.driver, "Size": "N/A"}) + "\n"
                    for v in self.volumes
                )
            case ("network", "ls", *_):
                return "".join(
                    json.dumps({"ID": n.id, "Name": n.name, "Driver": n.driver, "CreatedAt": _WHEN})
                    + "\n"
                    for n in self.networks
                )
            case ("system", "df", "-v"):
                if self.df_fails:
                    raise DockerError("df failed")
                return self._df()
            case ("rm", cid):
                return self._rm(cid)
            case ("rmi", ref):
                return self._rmi(ref)
            case ("volume", "rm", name):
                return self._volume_rm(name)
            case ("network", "rm", nid):
                return self._network_rm(nid)
        msg = f"FakeDocker does not know the command: docker {' '.join(call)}"
        raise AssertionError(msg)

    # -- rendering ---------------------------------------------------------------------

    @staticmethod
    def _inspect(c: ContainerInfo) -> dict[str, object]:
        return {
            "Id": c.id,
            "Name": "/" + c.name,
            "Created": "2024-03-05T09:30:00.000000000Z",
            "Image": "sha256:" + c.image_id,
            "State": {"Status": c.state},
            "SizeRw": c.size_rw,
            "Config": {"Image": c.image_name},
            "HostConfig": {"NetworkMode": c.networks[0] if c.networks else "default"},
            "NetworkSettings": {"Networks": {n: {} for n in c.networks}},
            "Mounts": [{"Type": "volume", "Name": v, "Destination": "/x"} for v in c.volumes],
        }

    @staticmethod
    def _image_row(i: ImageInfo) -> dict[str, object]:
        return {
            "ID": "sha256:" + i.id,
            "Repository": i.repository,
            "Tag": i.tag,
            "Size": human(i.size),
            "CreatedAt": _WHEN,
            "Containers": "N/A",
        }

    def _df(self) -> str:
        links = {v.name: sum(v.name in c.volumes for c in self.containers) for v in self.volumes}
        rows = "".join(
            # Docker pads the name column to the longest name, never less than a gap.
            f"{v.name}  {links[v.name]:<8}{human(self.volume_sizes.get(v.name))}\n"
            for v in self.volumes
        )
        return (
            "Images space usage:\n\nREPOSITORY   TAG   IMAGE ID   SIZE\n\n"
            "Local Volumes space usage:\n\nVOLUME NAME" + " " * 31 + "LINKS     SIZE\n"
            f"{rows}\nBuild cache usage: 0B\n"
        )

    # -- removal, with docker's own refusals -------------------------------------------

    def _rm(self, cid: str) -> str:
        box = next((c for c in self.containers if c.id == cid), None)
        if box is None:
            raise DockerError(f"No such container: {cid}")
        if box.state not in {"exited", "created", "dead"}:
            raise DockerError("cannot remove a running container; stop it first or use -f")
        self.containers.remove(box)
        return cid + "\n"

    def _rmi(self, ref: str) -> str:
        rows = [i for i in self.images if f"{i.repository}:{i.tag}" == ref or i.id == ref]
        if not rows:
            raise DockerError(f"No such image: {ref}")
        if any(c.image_id == rows[0].id for c in self.containers):
            raise DockerError("conflict: unable to delete image (image is being used)")
        by_id = ref == rows[0].id and len([i for i in self.images if i.id == ref]) > 1
        if by_id:
            raise DockerError("conflict: image is referenced in multiple repositories")
        self.images.remove(rows[0])
        return f"Untagged: {ref}\n"

    def _volume_rm(self, name: str) -> str:
        volume = next((v for v in self.volumes if v.name == name), None)
        if volume is None:
            raise DockerError(f"No such volume: {name}")
        if any(name in c.volumes for c in self.containers):
            raise DockerError("volume is in use")
        self.volumes.remove(volume)
        return name + "\n"

    def _network_rm(self, nid: str) -> str:
        net = next((n for n in self.networks if n.id == nid), None)
        if net is None:
            raise DockerError(f"No such network: {nid}")
        if net.name in {"bridge", "host", "none"}:
            raise DockerError(f"{net.name} is a pre-defined network and cannot be removed")
        if any(net.name in c.networks for c in self.containers):
            raise DockerError("network has active endpoints")
        self.networks.remove(net)
        return nid + "\n"

    # -- conveniences for tests ---------------------------------------------------------

    def stop_everything(self) -> None:
        self.containers = [replace(c, state="exited") for c in self.containers]
