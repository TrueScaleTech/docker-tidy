from collections.abc import Sequence
from dataclasses import replace

import pytest

import scenario
from docker_tidy.collect import collect_inventory
from docker_tidy.models import ContainerInfo, Kind, Usage
from docker_tidy.parse import ParseError
from docker_tidy.runner import DockerError
from fakes import FakeDocker


def test_the_whole_scenario_is_read_through_docker_commands() -> None:
    inv = collect_inventory(FakeDocker())
    by_name = {r.name: r for r in inv.resources}
    assert by_name["web-1"].usage is Usage.IN_USE
    assert by_name["old-job"].usage is Usage.UNUSED
    assert by_name["alpine:3.19"].usage is Usage.UNUSED
    assert by_name["orphan-data"].size == 1_200_000_000
    assert by_name["scratch-net"].usage is Usage.UNUSED
    assert by_name["bridge"].usage is Usage.PROTECTED


def test_collecting_never_changes_anything() -> None:
    docker = FakeDocker()
    collect_inventory(docker)
    removing = {"rm", "rmi", "prune"}
    for call in docker.calls:
        assert not removing & set(call)
        assert call[:2] != ("volume", "rm")
        assert call[:2] != ("network", "rm")
        assert "--force" not in call
        assert "-f" not in call


def test_a_dead_daemon_gives_docker_error_with_its_own_message() -> None:
    docker = FakeDocker()
    docker.down = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock."
    with pytest.raises(DockerError, match="Cannot connect"):
        collect_inventory(docker)
    assert len(docker.calls) == 1  # it stopped at the first question


def test_missing_volume_sizes_do_not_stop_the_scan() -> None:
    docker = FakeDocker()
    docker.df_fails = True
    inv = collect_inventory(docker)
    assert all(v.size is None for v in inv.of_kind(Kind.VOLUME))
    assert inv.of_kind(Kind.IMAGE)  # everything else is still there


def test_an_empty_host_needs_no_inspect_call() -> None:
    docker = FakeDocker(containers=[], images=[], volumes=[], networks=[])
    assert collect_inventory(docker).resources == ()
    assert not any(call[:2] == ("container", "inspect") for call in docker.calls)


def test_many_containers_are_inspected_in_batches() -> None:
    template: ContainerInfo = scenario.containers()[2]
    many = [replace(template, id=f"{n:064x}", name=f"job-{n}") for n in range(120)]
    docker = FakeDocker(containers=many)
    inv = collect_inventory(docker)
    inspects = [call for call in docker.calls if call[:2] == ("container", "inspect")]
    assert len(inspects) == 3
    assert all(len(call) - 3 <= 50 for call in inspects)
    assert len(inv.of_kind(Kind.CONTAINER)) == 120


def test_output_that_is_not_json_is_a_parse_error() -> None:
    class Broken(FakeDocker):
        def run(self, args: Sequence[str]) -> str:
            if tuple(args[:2]) == ("image", "ls"):
                return "REPOSITORY TAG\nnot json\n"
            return super().run(args)

    with pytest.raises(ParseError):
        collect_inventory(Broken())
