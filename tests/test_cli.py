import io
import json
from collections.abc import Sequence

import pytest

import scenario
from docker_tidy import __version__
from docker_tidy.cli import main
from docker_tidy.runner import DockerError
from fakes import FakeDocker


def run(*argv: str, docker: FakeDocker | None = None) -> tuple[int, str]:
    out = io.StringIO()
    code = main(list(argv), runner=docker or FakeDocker(), out=out)
    return code, out.getvalue()


# --- basics -------------------------------------------------------------------------


def test_no_command_prints_help() -> None:
    code, text = run()
    assert code == 0
    assert "report" in text
    assert "clean" in text


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        main(["--version"])
    assert stop.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_an_unknown_kind_is_rejected_by_the_parser(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        main(["report", "--kind", "pizza"])
    assert stop.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


# --- report -------------------------------------------------------------------------


def test_report_lists_only_unused_things_by_default() -> None:
    code, text = run("report")
    assert code == 0
    assert "old-job" in text
    assert "alpine:3.19" in text
    assert "orphan-data" in text
    assert "scratch-net" in text
    for kept in ("web-1", "db-1", "nginx:1.27", "web-data", "app-net"):
        assert kept not in text


def test_report_all_shows_what_is_in_use_and_why() -> None:
    code, text = run("report", "--all")
    assert code == 0
    assert "web-1" in text
    assert "used by running container web-1" in text
    assert "built-in network" in text


def test_report_can_be_limited_to_a_kind() -> None:
    _, text = run("report", "--kind", "volume")
    assert "Volumes" in text
    assert "Images" not in text
    assert "orphan-data" in text
    assert "layers shared" not in text  # the image caveat only appears with images


def test_report_totals_the_unused_space_and_warns_about_shared_layers() -> None:
    _, text = run("report")
    assert "Reclaimable: 1.6 GB" in text
    assert "layers shared" in text


def test_report_json_is_valid_and_complete() -> None:
    code, text = run("report", "--json")
    assert code == 0
    body = json.loads(text)
    assert body["version"] == __version__
    assert body["reclaimable_bytes"] == 1_562_466_048
    kinds = {r["kind"] for r in body["resources"]}
    assert kinds == {"container", "image", "volume", "network"}
    assert all(r["usage"] == "unused" for r in body["resources"])
    orphan = next(r for r in body["resources"] if r["name"] == "orphan-data")
    assert orphan["size_bytes"] == 1_200_000_000
    assert orphan["id"] == "orphan-data"


def test_report_json_with_all_includes_in_use() -> None:
    _, text = run("report", "--json", "--all")
    usages = {r["usage"] for r in json.loads(text)["resources"]}
    assert usages == {"unused", "in use", "protected"}


def test_report_on_an_empty_host_says_so() -> None:
    docker = FakeDocker(containers=[], images=[], volumes=[], networks=[])
    code, text = run("report", docker=docker)
    assert code == 0
    assert "nothing to clean" in text
    assert "Reclaimable: 0 B" in text


# --- clean --------------------------------------------------------------------------


def test_clean_defaults_to_a_dry_run_and_changes_nothing() -> None:
    docker = FakeDocker()
    code, text = run("clean", docker=docker)
    assert code == 0
    assert "Dry run" in text
    assert f"docker rm {scenario.C_JOB}" in text
    assert f"docker rmi {scenario.UNTAGGED}" in text
    assert "--yes" in text
    assert len(docker.containers) == 3  # nothing was removed


def test_clean_selects_only_stopped_containers_and_untagged_images_by_default() -> None:
    _, text = run("clean")
    assert "volume rm" not in text
    assert "network rm" not in text
    assert "alpine" not in text
    assert "example/app" not in text


def test_clean_yes_removes_the_selection() -> None:
    docker = FakeDocker()
    code, text = run("clean", "--yes", docker=docker)
    assert code == 0
    assert "removed" in text
    assert "old-job" not in {c.name for c in docker.containers}
    assert scenario.UNTAGGED not in {i.id for i in docker.images}
    assert "alpine:3.19" in {f"{i.repository}:{i.tag}" for i in docker.images}  # not selected


def test_clean_by_kind_takes_every_unused_resource_of_that_kind() -> None:
    docker = FakeDocker()
    code, _ = run("clean", "--kind", "volume", "--yes", docker=docker)
    assert code == 0
    assert {v.name for v in docker.volumes} == {"web-data", "db-data", scenario.ANON_USED}


def test_clean_by_name_takes_just_that_resource() -> None:
    docker = FakeDocker()
    code, text = run("clean", "--name", "alpine:3.19", "--name", "scratch-net", docker=docker)
    assert code == 0
    assert "docker rmi alpine:3.19" in text
    assert f"docker network rm {scenario.N_SCRATCH}" in text
    assert text.count("docker ") == 2


def test_clean_by_name_accepts_one_tag_of_a_multi_tag_image() -> None:
    _, text = run("clean", "--name", "example/app:old")
    assert "docker rmi example/app:latest" in text
    assert "docker rmi example/app:old" in text


def test_clean_by_name_accepts_the_start_of_an_id() -> None:
    _, text = run("clean", "--name", scenario.C_JOB[:12])
    assert f"docker rm {scenario.C_JOB}" in text


def test_clean_refuses_something_in_use(capsys: pytest.CaptureFixture[str]) -> None:
    docker = FakeDocker()
    code, text = run("clean", "--name", "web-data", "--yes", docker=docker)
    assert code == 2
    assert text == ""
    assert "Will not remove volume 'web-data': mounted by web-1" in capsys.readouterr().err
    assert "web-data" in {v.name for v in docker.volumes}


def test_clean_refuses_a_protected_network(capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = run("clean", "--name", "bridge", "--yes")
    assert code == 2
    assert "built-in network" in capsys.readouterr().err


def test_clean_refuses_a_name_that_matches_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = run("clean", "--name", "does-not-exist")
    assert code == 2
    assert "No container, image, volume or network matches 'does-not-exist'" in (
        capsys.readouterr().err
    )


def test_one_blocked_name_stops_the_whole_request() -> None:
    docker = FakeDocker()
    code, _ = run("clean", "--name", "alpine:3.19", "--name", "web-1", "--yes", docker=docker)
    assert code == 2
    assert "alpine:3.19" in {f"{i.repository}:{i.tag}" for i in docker.images}


def test_clean_with_nothing_to_do_says_so() -> None:
    docker = FakeDocker(containers=[], images=[], volumes=[], networks=[])
    code, text = run("clean", "--yes", docker=docker)
    assert code == 0
    assert text.strip() == "Nothing to remove."


def test_clean_reports_a_failed_removal_with_exit_code_1() -> None:
    class Refusing(FakeDocker):
        def run(self, args: Sequence[str]) -> str:
            if tuple(args)[:1] == ("rm",):
                raise DockerError("conflict: unable to remove")
            return super().run(args)

    code, text = run("clean", "--yes", docker=Refusing())
    assert code == 1
    assert "failed" in text
    assert "conflict: unable to remove" in text


def test_dry_run_and_yes_cannot_be_combined(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        main(["clean", "--dry-run", "--yes"])
    assert stop.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


# --- errors -------------------------------------------------------------------------


def test_docker_not_reachable_is_a_one_line_error_not_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    docker = FakeDocker()
    docker.down = "Cannot connect to the Docker daemon."
    code, text = run("report", docker=docker)
    assert code == 1
    assert text == ""
    assert capsys.readouterr().err == "docker-tidy: Cannot connect to the Docker daemon.\n"


def test_unreadable_docker_output_is_a_one_line_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Garbled(FakeDocker):
        def run(self, args: Sequence[str]) -> str:
            if tuple(args)[:2] == ("volume", "ls"):
                return "NAME DRIVER\nnot json\n"
            return super().run(args)

    code, _ = run("report", docker=Garbled())
    assert code == 1
    assert "not JSON" in capsys.readouterr().err
