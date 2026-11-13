"""The two dialogs: confirm a cleanup, and read what happened."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class ConfirmScreen(ModalScreen[bool]):
    """Shows the exact commands. Focus starts on Cancel, so Enter alone never deletes."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("y", "confirm", "Remove"),
    ]

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    #dialog { width: 90%; max-width: 110; height: auto; max-height: 80%;
              border: thick $error; background: $surface; padding: 1 2; }
    #commands { height: auto; max-height: 14; border: round $primary; padding: 0 1; }
    .warn { color: $error; text-style: bold; }
    #buttons { height: auto; margin-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, commands: Sequence[str], summary: str, warn_volumes: bool) -> None:
        super().__init__()
        self._commands = list(commands)
        self._summary = summary
        self._warn_volumes = warn_volumes

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            count = len(self._commands)
            yield Label(Text(f"Run {count} command{'s' if count != 1 else ''}?  {self._summary}"))
            if self._warn_volumes:
                yield Label("Removing a volume deletes its data for good.", classes="warn")
            with VerticalScroll(id="commands"):
                yield Static(Text("\n".join(self._commands)))
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel", variant="primary")
                yield Button("Remove", id="remove", variant="error")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "remove")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)


class ResultScreen(ModalScreen[None]):
    """What the cleanup did, one line per command."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close"),
    ]

    DEFAULT_CSS = """
    ResultScreen { align: center middle; }
    #result { width: 90%; max-width: 110; height: auto; max-height: 80%;
              border: thick $primary; background: $surface; padding: 1 2; }
    #lines { height: auto; max-height: 16; }
    #close-row { height: auto; margin-top: 1; align-horizontal: right; }
    """

    def __init__(self, headline: str, lines: Sequence[str]) -> None:
        super().__init__()
        self._headline = headline
        self._lines = list(lines)

    def compose(self) -> ComposeResult:
        with Vertical(id="result"):
            yield Label(Text(self._headline))
            with VerticalScroll(id="lines"):
                yield Static(Text("\n".join(self._lines)))
            with Horizontal(id="close-row"):
                yield Button("Close", id="close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#close", Button).focus()

    def on_button_pressed(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
