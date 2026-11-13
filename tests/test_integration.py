"""Against a real Docker daemon. Skipped unless one is running, and by default.

Run with `pytest -m integration`. Everything it creates is named `docker-tidy-test-*`
and removed afterwards, and the cleanup commands it runs name those resources one by
one, so it is safe to run on a machine that has other containers and images.

This is the one place that checks the assumptions the rest of the program makes about
what `docker` prints. The unit tests read recorded output; this reads the real thing.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from docker_tidy.cli import main
from docker_tidy.collect import collect_inventory
from docker_tidy.models import Inventory, Kind, Resource, Usage
from docker_tidy.runner import SubprocessRunner

BASE_IMAGE = "busybox:1.36"


def _docker_available() -> bool:
    path = shutil.which("docker")
    if path is None:
        return False
    try:
        done = subprocess.run(  # noqa: S603 - fixed arguments
            [path, "info"], capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _docker_available(), reason="no Docker daemon is running"),
]


def docker(*args: str, check: bool = True, stdin: str | None = None) -> str:
    done = subprocess.run(  # noqa: S603 - the test builds these arguments itself
        ["docker", *args],  # noqa: S607 - resolved through PATH on purpose
        capture_output=True,
        text=True,
        input=stdin,
        timeout=300,
        check=False,
    )
    if check and done.returncode != 0:
        msg = f"docker {' '.join(args)} failed: {done.stderr}"
        raise AssertionError(msg)
    return done.stdout


@dataclass(frozen=True)
class Host:
    prefix: str
    running: str  # a running container
    stopped: str  # a finished container
    used_volume: str  # mounted by the running container
    orphan_volume: str  # mounted by nothing
    used_network: str
    orphan_network: str
    unused_image: str  # tagged, no container uses it


@pytest.fixture
def host() -> Iterator[Host]:
    run_id = uuid.uuid4().hex[:8]
    prefix = f"docker-tidy-test-{run_id}"
    h = Host(
        prefix=prefix,
        running=f"{prefix}-running",
        stopped=f"{prefix}-stopped",
        used_volume=f"{prefix}-used-vol",
        orphan_volume=f"{prefix}-orphan-vol",
        used_network=f"{prefix}-used-net",
        orphan_network=f"{prefix}-orphan-net",
        unused_image=f"docker-tidy-test/{run_id}:unused",
    )
    docker("pull", "--quiet", BASE_IMAGE)
    docker("network", "create", h.used_network)
    docker("network", "create", h.orphan_network)
    docker("volume", "create", h.used_volume)
    docker("volume", "create", h.orphan_volume)
    # A new image id that nothing uses: busybox plus one layer.
    docker(
        "build",
        "--quiet",
        "-t",
        h.unused_image,
        "-",
        stdin=f"FROM {BASE_IMAGE}\nRUN echo {run_id} > /id\n",
    )
    docker(
        "run", "-d", "--name", h.running, "--network", h.used_network,
        "-v", f"{h.used_volume}:/data", BASE_IMAGE, "sleep", "600",
    )  # fmt: skip
    docker("run", "--name", h.stopped, BASE_IMAGE, "true")
    try:
        yield h
    finally:
        # Forcing is fine here: these are the test's own names, and this is teardown.
        for name in (h.running, h.stopped):
            docker("rm", "-f", name, check=False)
        for volume in (h.used_volume, h.orphan_volume):
            docker("volume", "rm", "-f", volume, check=False)
        for network in (h.used_network, h.orphan_network):
            docker("network", "rm", network, check=False)
        docker("rmi", "-f", h.unused_image, check=False)


def find(inv: Inventory, kind: Kind, name: str) -> Resource:
    matches = [r for r in inv.of_kind(kind) if name in r.name]
    assert len(matches) == 1, f"expected one {kind.value} named {name}, found {matches}"
    return matches[0]


def test_the_real_output_is_understood(host: Host) -> None:
    inv = collect_inventory(SubprocessRunner())

    assert find(inv, Kind.CONTAINER, host.running).usage is Usage.IN_USE
    stopped = find(inv, Kind.CONTAINER, host.stopped)
    assert stopped.usage is Usage.UNUSED
    assert stopped.preselected

    assert find(inv, Kind.VOLUME, host.used_volume).usage is Usage.IN_USE
    assert find(inv, Kind.VOLUME, host.orphan_volume).usage is Usage.UNUSED

    assert find(inv, Kind.NETWORK, host.used_network).usage is Usage.IN_USE
    assert find(inv, Kind.NETWORK, host.orphan_network).usage is Usage.UNUSED

    unused_image = find(inv, Kind.IMAGE, host.unused_image)
    assert unused_image.usage is Usage.UNUSED
    assert unused_image.size is not None
    assert unused_image.size > 0

    # The base image is used by the running container, so it must never be offered.
    base = find(inv, Kind.IMAGE, BASE_IMAGE)
    assert base.usage is Usage.IN_USE

    builtin = {r.name: r for r in inv.of_kind(Kind.NETWORK)}
    for name in ("bridge", "host", "none"):
        assert builtin[name].usage is Usage.PROTECTED


def test_volume_sizes_are_read_from_the_real_df_table(host: Host) -> None:
    inv = collect_inventory(SubprocessRunner())
    assert find(inv, Kind.VOLUME, host.orphan_volume).size is not None


def test_cleaning_by_name_removes_only_those_and_spares_the_running_service(
    host: Host,
) -> None:
    names = [host.stopped, host.orphan_volume, host.orphan_network, host.unused_image]
    argv = ["clean", "--yes"]
    for name in names:
        argv += ["--name", name]
    out = io.StringIO()

    assert main(argv, out=out) == 0, out.getvalue()

    inv = collect_inventory(SubprocessRunner())
    for kind, name in [
        (Kind.CONTAINER, host.stopped),
        (Kind.VOLUME, host.orphan_volume),
        (Kind.NETWORK, host.orphan_network),
        (Kind.IMAGE, host.unused_image),
    ]:
        assert not [r for r in inv.of_kind(kind) if name in r.name], f"{name} should be gone"

    assert find(inv, Kind.CONTAINER, host.running).usage is Usage.IN_USE
    assert find(inv, Kind.VOLUME, host.used_volume).usage is Usage.IN_USE
    assert "Up" in docker("ps", "--filter", f"name={host.running}", "--format", "{{.Status}}")


def test_the_real_cli_refuses_to_touch_what_is_in_use(host: Host) -> None:
    for name in (host.running, host.used_volume, host.used_network):
        assert main(["clean", "--yes", "--name", name], out=io.StringIO()) == 2
    assert docker("volume", "ls", "--quiet", "--filter", f"name={host.used_volume}").strip()


def test_a_dry_run_changes_nothing(host: Host) -> None:
    out = io.StringIO()
    assert main(["clean", "--kind", "volume", "--name", host.stopped], out=out) == 0
    assert "Dry run" in out.getvalue()
    assert docker("volume", "ls", "--quiet", "--filter", f"name={host.orphan_volume}").strip()
    assert docker("ps", "-a", "--quiet", "--filter", f"name={host.stopped}").strip()
