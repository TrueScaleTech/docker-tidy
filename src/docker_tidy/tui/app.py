"""The terminal screen. Drawing and key handling only: decisions live in `viewmodel`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Footer, Header, Static, TabbedContent, TabPane

from docker_tidy import __version__
from docker_tidy.analysis import NotRemovableError, PlannedCommand
from docker_tidy.cleaner import Outcome, Status, run_cleanup
from docker_tidy.collect import collect_inventory
from docker_tidy.display import KIND_TITLES
from docker_tidy.models import KIND_ORDER, Inventory, Kind, Resource, Usage
from docker_tidy.parse import ParseError
from docker_tidy.runner import DockerError, Runner
from docker_tidy.tui.screens import ConfirmScreen, ResultScreen
from docker_tidy.viewmodel import COLUMNS, Selection, row_cells, tab_title


class DockerTidyApp(App[None]):
    TITLE = "docker-tidy"
    SUB_TITLE = __version__

    CSS = """
    #status { height: auto; padding: 0 1; background: $boost; }
    #status.error { color: $error; text-style: bold; }
    #detail { height: auto; min-height: 1; padding: 0 1; color: $text-muted; }
    DataTable { height: 1fr; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("space", "toggle_row", "Select"),
        Binding("a", "tick_all", "Select all"),
        Binding("n", "untick_all", "Select none"),
        Binding("c", "clean", "Clean..."),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        runner: Runner,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        super().__init__()
        self._runner = runner
        self._now = now
        self.selection: Selection | None = None
        self.busy = False
        self._rows: dict[Kind, list[Resource]] = {kind: [] for kind in KIND_ORDER}

    # -- layout ------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Reading Docker...", id="status")
        with TabbedContent(id="tabs"):
            for kind in KIND_ORDER:
                with TabPane(KIND_TITLES[kind], id=f"tab-{kind.value}"):
                    yield DataTable[Text](id=f"table-{kind.value}", cursor_type="row")
        yield Static("", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        for kind in KIND_ORDER:
            table = self._table(kind)
            table.add_columns(*COLUMNS)
        self.action_refresh()

    # -- helpers -----------------------------------------------------------------------

    def _table(self, kind: Kind) -> DataTable[Text]:
        return self.query_one(f"#table-{kind.value}", DataTable)

    def _active_kind(self) -> Kind:
        active = self.query_one("#tabs", TabbedContent).active
        return Kind(active.removeprefix("tab-"))

    def _current(self) -> Resource | None:
        kind = self._active_kind()
        row = self._table(kind).cursor_row
        rows = self._rows[kind]
        return rows[row] if 0 <= row < len(rows) else None

    def _say(self, text: str, *, error: bool = False) -> None:
        status = self.query_one("#status", Static)
        status.update(Text(text))
        status.set_class(error, "error")

    def _cells(self, resource: Resource) -> list[Text]:
        ticked = self.selection is not None and self.selection.is_ticked(resource)
        style = "dim" if resource.usage is not Usage.UNUSED else ("bold green" if ticked else "")
        return [Text(cell, style=style) for cell in row_cells(resource, ticked, self._now())]

    def _update_summary(self) -> None:
        if self.selection is not None:
            self._say(self.selection.summary())

    # -- loading -----------------------------------------------------------------------

    def action_refresh(self) -> None:
        if self.busy:
            return
        self.busy = True
        self._say("Reading Docker...")
        self._load()

    @work(thread=True, exclusive=True)
    def _load(self) -> None:
        try:
            inventory = collect_inventory(self._runner)
        except (DockerError, ParseError) as exc:
            self.call_from_thread(self._show_error, str(exc))
        else:
            self.call_from_thread(self._show_inventory, inventory)

    def _show_error(self, message: str) -> None:
        self.busy = False
        self._say(f"{message}   (press r to try again)", error=True)

    def _show_inventory(self, inventory: Inventory) -> None:
        self.busy = False
        if self.selection is None:
            self.selection = Selection(inventory)
        else:
            self.selection.replace_inventory(inventory)
        tabs = self.query_one("#tabs", TabbedContent)
        for kind in KIND_ORDER:
            table = self._table(kind)
            keep = table.cursor_row
            table.clear()
            self._rows[kind] = list(inventory.of_kind(kind))
            for resource in self._rows[kind]:
                table.add_row(*self._cells(resource), key=f"{resource.kind}:{resource.key}")
            if self._rows[kind]:
                table.move_cursor(row=min(keep, len(self._rows[kind]) - 1))
            tabs.get_tab(f"tab-{kind.value}").label = Text(tab_title(kind, inventory))
        self._update_summary()
        self._show_detail()

    # -- the detail line ---------------------------------------------------------------

    @on(DataTable.RowHighlighted)
    def _show_detail(self) -> None:
        resource = self._current()
        detail = self.query_one("#detail", Static)
        if resource is None:
            detail.update("")
            return
        parts = [resource.name, resource.reason]
        if resource.note:
            parts.append(resource.note)
        detail.update(Text("  |  ".join(parts)))

    @on(TabbedContent.TabActivated)
    def _tab_changed(self) -> None:
        self._show_detail()

    # -- selecting ---------------------------------------------------------------------

    def _redraw_checkboxes(self, kinds: list[Kind]) -> None:
        for kind in kinds:
            table = self._table(kind)
            for index, resource in enumerate(self._rows[kind]):
                table.update_cell_at(Coordinate(index, 0), self._cells(resource)[0])
                for column, cell in enumerate(self._cells(resource)[1:], start=1):
                    table.update_cell_at(Coordinate(index, column), cell)
        self._update_summary()

    def action_toggle_row(self) -> None:
        resource = self._current()
        if self.selection is None or self.busy or resource is None:
            return
        if not self.selection.toggle(resource):
            self.notify(f"{resource.name}: {resource.reason}", severity="warning")
            return
        self._redraw_checkboxes([resource.kind])

    def action_tick_all(self) -> None:
        if self.selection is None or self.busy:
            return
        kind = self._active_kind()
        self.selection.tick_all(kind)
        if kind is Kind.VOLUME:
            self.notify("Removing a volume deletes its data for good.", severity="warning")
        self._redraw_checkboxes([kind])

    def action_untick_all(self) -> None:
        if self.selection is None or self.busy:
            return
        self.selection.untick_all(self._active_kind())
        self._redraw_checkboxes([self._active_kind()])

    # -- cleaning ----------------------------------------------------------------------

    def action_clean(self) -> None:
        if self.selection is None or self.busy:
            return
        try:
            plan = self.selection.plan()
        except NotRemovableError as exc:  # pragma: no cover - ticks are limited to unused
            self.notify(str(exc), severity="error")
            return
        if not plan:
            self.notify("Nothing is selected.", severity="information")
            return
        screen = ConfirmScreen(
            [step.text for step in plan],
            self.selection.summary(),
            self.selection.has_ticked_volumes(),
        )
        self.push_screen(screen, lambda confirmed: self._confirmed(confirmed, plan))

    def _confirmed(self, confirmed: bool | None, plan: tuple[PlannedCommand, ...]) -> None:
        if not confirmed:
            return
        self.busy = True
        self._say(f"Removing {len(plan)} item(s)...")
        self._clean(plan)

    @work(thread=True, exclusive=True)
    def _clean(self, plan: tuple[PlannedCommand, ...]) -> None:
        try:
            outcomes = run_cleanup(plan, self._runner)
        except (DockerError, ParseError) as exc:
            self.call_from_thread(self._clean_failed, str(exc))
        else:
            self.call_from_thread(self._clean_done, outcomes)

    def _clean_failed(self, message: str) -> None:
        self.busy = False
        self._say(f"{message}   (press r to try again)", error=True)

    def _clean_done(self, outcomes: list[Outcome]) -> None:
        removed = sum(o.status is Status.REMOVED for o in outcomes)
        problems = [o for o in outcomes if o.status is not Status.REMOVED]
        headline = f"{removed} of {len(outcomes)} removed."
        lines = [
            f"{o.status.value:<8}{o.command.text}" + (f"  ({o.message})" if o.message else "")
            for o in outcomes
        ]
        self.busy = False
        self.push_screen(
            ResultScreen(headline + (" Some could not be." if problems else ""), lines),
            lambda _: self.action_refresh(),
        )


def run(runner: Runner) -> int:  # pragma: no cover - needs a real terminal
    """Open the screen. Returns the exit code for the command line."""
    DockerTidyApp(runner).run()
    return 0
