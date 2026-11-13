from collections.abc import Sequence

import scenario
from docker_tidy.analysis import PlannedCommand, plan_cleanup
from docker_tidy.cleaner import Status, run_cleanup
from docker_tidy.collect import collect_inventory
from docker_tidy.models import ContainerInfo, Kind
from docker_tidy.runner import DockerError
from fakes import FakeDocker


def _unused_plan(docker: FakeDocker) -> tuple[PlannedCommand, ...]:
    inv = collect_inventory(docker)
    return plan_cleanup(r for r in inv.resources if r.removable)


def test_everything_unused_is_removed_and_everything_in_use_stays() -> None:
    docker = FakeDocker()
    outcomes = run_cleanup(_unused_plan(docker), docker)

    assert {o.status for o in outcomes} == {Status.REMOVED}
    names = {c.name for c in docker.containers}
    assert names == {"web-1", "db-1"}
    assert {v.name for v in docker.volumes} == {"web-data", "db-data", scenario.ANON_USED}
    assert {n.name for n in docker.networks} == {"bridge", "host", "none", "app-net"}
    assert {f"{i.repository}:{i.tag}" for i in docker.images} == {
        "nginx:1.27",
        "postgres:16",
        "example/job:1.0",
    }


def test_removing_a_container_frees_its_image_for_the_next_pass() -> None:
    docker = FakeDocker()
    run_cleanup(_unused_plan(docker), docker)
    job = next(r for r in collect_inventory(docker).of_kind(Kind.IMAGE) if "example/job" in r.name)
    assert job.removable  # old-job is gone, so nothing holds the image any more


def test_a_resource_that_started_being_used_after_the_scan_is_skipped() -> None:
    docker = FakeDocker()
    plan = _unused_plan(docker)
    # Between the scan and the cleanup, a new container mounts the orphan volume.
    docker.containers.append(
        ContainerInfo("f" * 64, "late", scenario.NGINX, "nginx", "running", 0, None,
                      ("orphan-data",), ("app-net",))
    )  # fmt: skip
    outcomes = run_cleanup(plan, docker)

    orphan = next(o for o in outcomes if o.command.resource.name == "orphan-data")
    assert orphan.status is Status.SKIPPED
    assert "in use" in orphan.message
    assert "orphan-data" in {v.name for v in docker.volumes}
    assert ("volume", "rm", "orphan-data") not in docker.calls


def test_a_resource_that_vanished_after_the_scan_is_skipped() -> None:
    docker = FakeDocker()
    plan = _unused_plan(docker)
    docker.networks = [n for n in docker.networks if n.name != "scratch-net"]
    outcomes = run_cleanup(plan, docker)
    gone = next(o for o in outcomes if o.command.resource.name == "scratch-net")
    assert gone.status is Status.SKIPPED
    assert "no longer exists" in gone.message


class _Refusing(FakeDocker):
    """Docker refuses one specific command, as it would for a volume that is in use."""

    def __init__(self, refused: tuple[str, ...]) -> None:
        super().__init__()
        self._refused = refused

    def run(self, args: Sequence[str]) -> str:
        if tuple(args) == self._refused:
            self.calls.append(tuple(args))
            raise DockerError("conflict: unable to remove")
        return super().run(args)


def test_one_failure_is_reported_and_the_rest_still_run() -> None:
    docker = _Refusing(("rmi", "alpine:3.19"))
    outcomes = run_cleanup(_unused_plan(docker), docker)

    failed = [o for o in outcomes if o.status is Status.FAILED]
    assert [o.command.resource.name for o in failed] == ["alpine:3.19"]
    assert failed[0].message == "conflict: unable to remove"
    assert any(o.status is Status.REMOVED for o in outcomes)
    assert "orphan-data" not in {v.name for v in docker.volumes}


def test_an_empty_plan_only_reads() -> None:
    docker = FakeDocker()
    assert run_cleanup([], docker) == []


def test_nothing_the_cleaner_runs_forces_or_prunes() -> None:
    docker = FakeDocker()
    run_cleanup(_unused_plan(docker), docker)
    for call in docker.calls:
        assert not {"-f", "--force", "prune"} & set(call)
