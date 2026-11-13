from dataclasses import replace
from datetime import UTC, datetime

import pytest

import scenario
from docker_tidy.analysis import NotRemovableError, build_inventory
from docker_tidy.models import Inventory, Kind, Resource
from docker_tidy.viewmodel import Selection, row_cells, tab_title

NOW = datetime(2026, 11, 13, tzinfo=UTC)


def _inventory() -> Inventory:
    return build_inventory(
        scenario.containers(),
        scenario.images(),
        scenario.volumes(),
        scenario.networks(),
        scenario.VOLUME_SIZES,
    )


def _find(inv: Inventory, kind: Kind, name: str) -> Resource:
    (found,) = (r for r in inv.of_kind(kind) if r.name.startswith(name))
    return found


def test_it_starts_with_the_safe_defaults_ticked() -> None:
    sel = Selection(_inventory())
    names = {r.name for r in sel.ticked()}
    assert names == {"old-job", f"<untagged> {scenario.UNTAGGED[:12]}"}


def test_volumes_and_networks_are_never_ticked_at_the_start() -> None:
    sel = Selection(_inventory())
    assert not any(r.kind in (Kind.VOLUME, Kind.NETWORK) for r in sel.ticked())


def test_it_exposes_the_inventory_it_was_given() -> None:
    inv = _inventory()
    assert Selection(inv).inventory is inv


def test_toggle_ticks_and_unticks() -> None:
    inv = _inventory()
    sel = Selection(inv)
    alpine = _find(inv, Kind.IMAGE, "alpine")
    assert sel.toggle(alpine)
    assert sel.is_ticked(alpine)
    assert sel.toggle(alpine)
    assert not sel.is_ticked(alpine)


@pytest.mark.parametrize(
    ("kind", "name"),
    [(Kind.CONTAINER, "web-1"), (Kind.IMAGE, "nginx"), (Kind.VOLUME, "web-data"),
     (Kind.NETWORK, "bridge"), (Kind.NETWORK, "app-net")],
)  # fmt: skip
def test_what_is_in_use_or_protected_cannot_be_ticked(kind: Kind, name: str) -> None:
    inv = _inventory()
    sel = Selection(inv)
    target = _find(inv, kind, name)
    assert not sel.toggle(target)
    assert not sel.is_ticked(target)


def test_tick_all_takes_only_the_unused_of_that_kind() -> None:
    inv = _inventory()
    sel = Selection(inv)
    sel.tick_all(Kind.VOLUME)
    ticked = {r.name for r in sel.ticked() if r.kind is Kind.VOLUME}
    assert ticked == {"orphan-data", f"{scenario.ANON_FREE[:12]} (anonymous)"}
    assert sel.has_ticked_volumes()


def test_untick_all_for_a_kind_or_for_everything() -> None:
    sel = Selection(_inventory())
    sel.tick_all(Kind.VOLUME)
    sel.untick_all(Kind.VOLUME)
    assert not sel.has_ticked_volumes()
    assert sel.ticked()  # the container and the image are still ticked
    sel.untick_all()
    assert sel.ticked() == []


def test_totals_and_summary() -> None:
    sel = Selection(_inventory())
    assert sel.ticked_size() == 2048 + 55_000_000
    assert sel.summary() == "2 selected, 55.0 MB to free. (1.6 GB unused in all)"
    sel.untick_all()
    assert sel.summary() == "Nothing selected. 1.6 GB could be freed."


def test_the_preview_is_the_exact_command_list() -> None:
    sel = Selection(_inventory())
    assert sel.preview_lines() == [
        f"docker rm {scenario.C_JOB}",
        f"docker rmi {scenario.UNTAGGED}",
    ]


def test_a_refreshed_scan_keeps_only_ticks_that_are_still_valid() -> None:
    inv = _inventory()
    sel = Selection(inv)
    sel.tick_all(Kind.VOLUME)

    # Between scans the orphan volume got mounted and old-job was removed.
    containers = [c for c in scenario.containers() if c.name != "old-job"]
    containers[0] = replace(containers[0], volumes=("web-data", "orphan-data"))
    fresh = build_inventory(
        containers,
        scenario.images(),
        scenario.volumes(),
        scenario.networks(),
        scenario.VOLUME_SIZES,
    )
    sel.replace_inventory(fresh)
    names = {r.name for r in sel.ticked()}
    assert "orphan-data" not in names
    assert "old-job" not in names
    assert f"{scenario.ANON_FREE[:12]} (anonymous)" in names


def test_row_cells_show_a_checkbox_for_unused_and_a_dash_for_the_rest() -> None:
    inv = _inventory()
    alpine = _find(inv, Kind.IMAGE, "alpine")
    web = _find(inv, Kind.CONTAINER, "web-1")
    assert row_cells(alpine, False, NOW)[0] == "[ ]"
    assert row_cells(alpine, True, NOW)[0] == "[x]"
    assert row_cells(web, False, NOW)[0] == " - "
    assert row_cells(alpine, False, NOW)[1:4] == ("alpine:3.19", "7.4 MB", "2y")


def test_row_cells_include_the_note_for_data_loss() -> None:
    orphan = _find(_inventory(), Kind.VOLUME, "orphan-data")
    assert "removing it deletes its data" in row_cells(orphan, False, NOW)[4]


def test_tab_titles_count_the_unused() -> None:
    inv = _inventory()
    assert tab_title(Kind.CONTAINER, inv) == "Containers (1)"
    assert tab_title(Kind.NETWORK, inv) == "Networks (1)"


def test_a_plan_cannot_be_built_from_something_that_became_unusable() -> None:
    inv = _inventory()
    sel = Selection(inv)
    web = _find(inv, Kind.CONTAINER, "web-1")
    sel._ticked.add((web.kind, web.key))  # forced past the guard on purpose
    with pytest.raises(NotRemovableError):
        sel.plan()
