"""The terminal screen, driven the way a person does: keys, tabs, buttons."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from rich.text import Text
from textual.pilot import Pilot
from textual.widgets import Button, DataTable, Static, TabbedContent

import scenario
from docker_tidy.models import Kind
from docker_tidy.runner import DockerError
from docker_tidy.tui.app import DockerTidyApp
from docker_tidy.tui.screens import ConfirmScreen, ResultScreen
from fakes import FakeDocker

NOW = datetime(2026, 11, 13, tzinfo=UTC)


def make_app(docker: FakeDocker | None = None) -> tuple[DockerTidyApp, FakeDocker]:
    docker = docker or FakeDocker()
    return DockerTidyApp(docker, now=lambda: NOW), docker


async def settle(app: DockerTidyApp, pilot: Pilot[None]) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


def table(app: DockerTidyApp, kind: Kind) -> DataTable[Text]:
    return app.query_one(f"#table-{kind.value}", DataTable)


def cells(app: DockerTidyApp, kind: Kind, row: int) -> list[str]:
    return [str(cell) for cell in table(app, kind).get_row_at(row)]


def showing(app: DockerTidyApp, screen_type: type[object]) -> bool:
    """Whether a screen of this type is on top. A function, so type checkers do not narrow."""
    return isinstance(app.screen, screen_type)


def status(app: DockerTidyApp) -> str:
    return str(app.query_one("#status", Static).content)


async def go_to(app: DockerTidyApp, pilot: Pilot[None], kind: Kind) -> None:
    app.query_one("#tabs", TabbedContent).active = f"tab-{kind.value}"
    await pilot.pause()


# --- opening ------------------------------------------------------------------------


async def test_it_reads_docker_on_start_and_fills_every_tab() -> None:
    app, docker = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert table(app, Kind.CONTAINER).row_count == 3
        assert table(app, Kind.IMAGE).row_count == 6
        assert table(app, Kind.VOLUME).row_count == 5
        assert table(app, Kind.NETWORK).row_count == 5
        assert docker.calls  # it did ask docker


async def test_the_safe_defaults_are_ticked_and_the_summary_says_so() -> None:
    app, _ = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert status(app).startswith("2 selected")
        job = cells(app, Kind.CONTAINER, 0)
        assert job[:2] == ["[x]", "old-job"]


async def test_tab_titles_show_how_much_is_unused() -> None:
    app, _ = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        label = app.query_one("#tabs", TabbedContent).get_tab("tab-volume").label
        assert str(label) == "Volumes (2)"


async def test_in_use_rows_are_listed_but_marked_with_a_dash() -> None:
    app, _ = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        names = {
            cells(app, Kind.CONTAINER, i)[1]: cells(app, Kind.CONTAINER, i)[0] for i in range(3)
        }
        assert names["web-1"].strip() == "-"
        assert names["old-job"] == "[x]"


# --- selecting ----------------------------------------------------------------------


async def test_space_unticks_and_ticks_the_row_under_the_cursor() -> None:
    app, _ = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert cells(app, Kind.CONTAINER, 0)[0] == "[x]"
        await pilot.press("space")
        assert cells(app, Kind.CONTAINER, 0)[0] == "[ ]"
        assert status(app).startswith("1 selected")
        await pilot.press("space")
        assert cells(app, Kind.CONTAINER, 0)[0] == "[x]"


async def test_space_on_something_in_use_changes_nothing() -> None:
    app, _ = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        table(app, Kind.CONTAINER).move_cursor(row=1)  # a running container
        await pilot.pause()
        before = status(app)
        await pilot.press("space")
        assert status(app) == before
        assert app.selection is not None
        assert all(r.removable for r in app.selection.ticked())


async def test_select_all_on_the_volumes_tab_takes_only_unused_volumes() -> None:
    app, _ = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await go_to(app, pilot, Kind.VOLUME)
        await pilot.press("a")
        assert app.selection is not None
        volumes = {r.name for r in app.selection.ticked() if r.kind is Kind.VOLUME}
        assert volumes == {"orphan-data", f"{scenario.ANON_FREE[:12]} (anonymous)"}
        assert app.selection.has_ticked_volumes()


async def test_select_none_clears_only_the_current_tab() -> None:
    app, _ = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("n")  # containers tab
        assert app.selection is not None
        kinds = {r.kind for r in app.selection.ticked()}
        assert kinds == {Kind.IMAGE}


# --- cleaning -----------------------------------------------------------------------


async def test_clean_with_nothing_selected_opens_no_dialog() -> None:
    app, _ = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        for kind in (Kind.CONTAINER, Kind.IMAGE):
            await go_to(app, pilot, kind)
            await pilot.press("n")
        await pilot.press("c")
        await pilot.pause()
        assert not showing(app, ConfirmScreen)


async def test_clean_shows_the_exact_commands_and_cancel_is_the_default() -> None:
    app, docker = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("c")
        await pilot.pause()
        assert showing(app, ConfirmScreen)
        assert app.screen.focused is app.screen.query_one("#cancel", Button)
        shown = str(app.screen.query_one("#commands Static", Static).content)
        assert f"docker rm {scenario.C_JOB}" in shown
        assert f"docker rmi {scenario.UNTAGGED}" in shown
        await pilot.press("enter")  # Enter on the default button must not delete
        await pilot.pause()
        assert not showing(app, ConfirmScreen)
        assert len(docker.containers) == 3


async def test_escape_cancels_and_nothing_is_removed() -> None:
    app, docker = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("c", "escape")
        await pilot.pause()
        assert not showing(app, ConfirmScreen)
        assert not any(call[0] in ("rm", "rmi") for call in docker.calls)


async def test_confirming_removes_the_items_shows_the_result_and_rescans() -> None:
    app, docker = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("c")
        await pilot.pause()
        await pilot.click("#remove")
        await settle(app, pilot)
        assert showing(app, ResultScreen)
        assert "old-job" not in {c.name for c in docker.containers}

        await pilot.click("#close")
        await settle(app, pilot)
        assert not showing(app, ResultScreen)
        # The rescan shows the host as it is now: the job's image is free to remove.
        assert table(app, Kind.CONTAINER).row_count == 2
        image_rows = [cells(app, Kind.IMAGE, i) for i in range(table(app, Kind.IMAGE).row_count)]
        job = next(row for row in image_rows if "example/job" in row[1])
        assert job[0] == "[ ]"


async def test_volumes_get_a_data_loss_warning_in_the_dialog() -> None:
    app, _ = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await go_to(app, pilot, Kind.VOLUME)
        await pilot.press("a", "c")
        await pilot.pause()
        assert showing(app, ConfirmScreen)
        warning = app.screen.query(".warn")
        assert len(warning) == 1
        assert "deletes its data" in str(warning.first(Static).content)


async def test_the_dialog_has_no_warning_when_no_volume_is_selected() -> None:
    app, _ = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("c")
        await pilot.pause()
        assert len(app.screen.query(".warn")) == 0


async def test_a_failed_removal_is_listed_in_the_result() -> None:
    class Refusing(FakeDocker):
        def run(self, args: Sequence[str]) -> str:
            if tuple(args)[:1] == ("rm",):
                raise DockerError("conflict: unable to remove")
            return super().run(args)

    app, _ = make_app(Refusing())
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("c")
        await pilot.pause()
        await pilot.click("#remove")
        await settle(app, pilot)
        assert showing(app, ResultScreen)
        lines = str(app.screen.query_one("#lines Static", Static).content)
        assert "failed" in lines
        assert "conflict: unable to remove" in lines


async def test_y_confirms_and_escape_closes_the_result() -> None:
    app, docker = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("c", "y")
        await settle(app, pilot)
        assert showing(app, ResultScreen)
        assert "old-job" not in {c.name for c in docker.containers}
        await pilot.press("escape")
        await settle(app, pilot)
        assert not showing(app, ResultScreen)


async def test_docker_dying_during_a_cleanup_is_reported_not_a_crash() -> None:
    app, docker = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("c")
        await pilot.pause()
        docker.down = "Cannot connect to the Docker daemon."
        await pilot.click("#remove")
        await settle(app, pilot)
        assert "Cannot connect to the Docker daemon." in status(app)
        assert not showing(app, ConfirmScreen)
        assert len(docker.containers) == 3  # nothing was removed


# --- errors and refreshing ----------------------------------------------------------


async def test_a_dead_daemon_shows_its_message_and_r_tries_again() -> None:
    docker = FakeDocker()
    docker.down = "Cannot connect to the Docker daemon."
    app, _ = make_app(docker)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert "Cannot connect to the Docker daemon." in status(app)
        assert "press r" in status(app)
        assert app.selection is None

        docker.down = None
        await pilot.press("r")
        await settle(app, pilot)
        assert table(app, Kind.CONTAINER).row_count == 3
        assert status(app).startswith("2 selected")


async def test_refresh_is_ignored_while_something_is_already_running() -> None:
    app, docker = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        asked = len(docker.calls)
        app.busy = True
        await pilot.press("r")
        await settle(app, pilot)
        assert len(docker.calls) == asked


async def test_refresh_keeps_ticks_that_are_still_valid() -> None:
    app, docker = make_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await go_to(app, pilot, Kind.IMAGE)
        table(app, Kind.IMAGE).move_cursor(row=2)
        await pilot.press("space")
        before = {r.name for r in (app.selection.ticked() if app.selection else [])}

        docker.networks = [n for n in docker.networks if n.name != "scratch-net"]
        await pilot.press("r")
        await settle(app, pilot)
        after = {r.name for r in (app.selection.ticked() if app.selection else [])}
        assert after == before


@pytest.mark.parametrize("key", ["space", "a", "n", "c"])
async def test_keys_do_nothing_before_docker_has_answered(key: str) -> None:
    docker = FakeDocker()
    docker.down = "no daemon"
    app, _ = make_app(docker)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press(key)
        await pilot.pause()
        assert not showing(app, ConfirmScreen)
        assert app.selection is None
