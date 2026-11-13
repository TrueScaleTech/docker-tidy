"""What the terminal screen shows and what the user has ticked. No Textual in here.

Keeping the decisions (what is selected, what the totals are, what the preview says)
apart from the widgets means they can be tested without opening a window.
"""

from __future__ import annotations

from datetime import datetime

from docker_tidy.analysis import PlannedCommand, plan_cleanup
from docker_tidy.display import KIND_TITLES, format_age, format_size
from docker_tidy.models import Inventory, Kind, Resource, Usage

COLUMNS = ("", "Name", "Size", "Age", "Why")

_Key = tuple[Kind, str]


class Selection:
    """The inventory plus the set of resources the user has ticked."""

    def __init__(self, inventory: Inventory) -> None:
        self._inventory = inventory
        self._ticked: set[_Key] = {
            _key(r) for r in inventory.resources if _tickable(r) and r.preselected
        }

    @property
    def inventory(self) -> Inventory:
        return self._inventory

    # -- reading -----------------------------------------------------------------------

    def is_ticked(self, resource: Resource) -> bool:
        return _key(resource) in self._ticked

    def ticked(self) -> list[Resource]:
        return [r for r in self._inventory.resources if _key(r) in self._ticked]

    def ticked_size(self) -> int:
        return sum(r.size or 0 for r in self.ticked())

    def has_ticked_volumes(self) -> bool:
        return any(r.kind is Kind.VOLUME for r in self.ticked())

    def summary(self) -> str:
        count = len(self.ticked())
        reclaimable = format_size(self._inventory.reclaimable())
        if count == 0:
            return f"Nothing selected. {reclaimable} could be freed."
        freed = format_size(self.ticked_size())
        return f"{count} selected, {freed} to free. ({reclaimable} unused in all)"

    # -- changing ----------------------------------------------------------------------

    def toggle(self, resource: Resource) -> bool:
        """Tick or untick. Returns False, changing nothing, for what cannot be ticked."""
        if not _tickable(resource):
            return False
        self._ticked ^= {_key(resource)}
        return True

    def tick_all(self, kind: Kind) -> None:
        self._ticked |= {_key(r) for r in self._inventory.of_kind(kind) if _tickable(r)}

    def untick_all(self, kind: Kind | None = None) -> None:
        if kind is None:
            self._ticked.clear()
        else:
            self._ticked -= {_key(r) for r in self._inventory.of_kind(kind)}

    def replace_inventory(self, inventory: Inventory) -> None:
        """Take a fresh scan. Ticks survive only for what is still there and still unused."""
        self._inventory = inventory
        still_valid = {_key(r) for r in inventory.resources if _tickable(r)}
        self._ticked &= still_valid

    # -- what the preview shows --------------------------------------------------------

    def plan(self) -> tuple[PlannedCommand, ...]:
        return plan_cleanup(self.ticked())

    def preview_lines(self) -> list[str]:
        return [step.text for step in self.plan()]


def _key(resource: Resource) -> _Key:
    return (resource.kind, resource.key)


def _tickable(resource: Resource) -> bool:
    return resource.usage is Usage.UNUSED


def row_cells(resource: Resource, ticked: bool, now: datetime) -> tuple[str, ...]:
    """The text of one table row. The first cell is the checkbox, or a lock."""
    tick = "[x]" if ticked else "[ ]"
    box = tick if resource.usage is Usage.UNUSED else " - "
    why = resource.reason if not resource.note else f"{resource.reason} - {resource.note}"
    return (box, resource.name, format_size(resource.size), format_age(resource.created, now), why)


def tab_title(kind: Kind, inventory: Inventory) -> str:
    unused = sum(1 for r in inventory.of_kind(kind) if r.removable)
    return f"{KIND_TITLES[kind]} ({unused})"
