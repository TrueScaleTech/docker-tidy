from dataclasses import replace

import pytest

import scenario
from docker_tidy.analysis import (
    NotRemovableError,
    build_inventory,
    plan_cleanup,
)
from docker_tidy.models import (
    ContainerInfo,
    ImageInfo,
    Inventory,
    Kind,
    Resource,
    Usage,
    VolumeInfo,
)


def _inventory(
    *,
    containers: list[ContainerInfo] | None = None,
    images: list[ImageInfo] | None = None,
    volume_sizes: dict[str, int] | None = None,
) -> Inventory:
    return build_inventory(
        containers if containers is not None else scenario.containers(),
        images if images is not None else scenario.images(),
        scenario.volumes(),
        scenario.networks(),
        volume_sizes if volume_sizes is not None else scenario.VOLUME_SIZES,
    )


def _find(inv: Inventory, kind: Kind, name: str) -> Resource:
    (found,) = (r for r in inv.of_kind(kind) if r.name.startswith(name))
    return found


# --- containers ---------------------------------------------------------------------


def test_running_containers_are_in_use_and_stopped_ones_are_unused() -> None:
    inv = _inventory()
    assert _find(inv, Kind.CONTAINER, "web-1").usage is Usage.IN_USE
    job = _find(inv, Kind.CONTAINER, "old-job")
    assert job.usage is Usage.UNUSED
    assert job.preselected


@pytest.mark.parametrize("state", ["running", "paused", "restarting", "removing", "weird"])
def test_any_state_that_is_not_clearly_stopped_is_left_alone(state: str) -> None:
    box = replace(scenario.containers()[2], state=state)
    inv = _inventory(containers=[box])
    assert _find(inv, Kind.CONTAINER, "old-job").usage is Usage.IN_USE


@pytest.mark.parametrize("state", ["exited", "created", "dead"])
def test_finished_or_never_started_containers_can_be_removed(state: str) -> None:
    box = replace(scenario.containers()[2], state=state)
    inv = _inventory(containers=[box])
    assert _find(inv, Kind.CONTAINER, "old-job").removable


# --- images -------------------------------------------------------------------------


def test_an_image_used_by_a_running_container_is_in_use() -> None:
    nginx = _find(_inventory(), Kind.IMAGE, "nginx:1.27")
    assert nginx.usage is Usage.IN_USE
    assert "web-1" in nginx.reason


def test_an_image_used_only_by_a_stopped_container_is_not_offered() -> None:
    job = _find(_inventory(), Kind.IMAGE, "example/job:1.0")
    assert job.usage is Usage.IN_USE
    assert "stopped container old-job" in job.reason


def test_an_image_becomes_unused_once_its_stopped_container_is_gone() -> None:
    remaining = [c for c in scenario.containers() if c.name != "old-job"]
    job = _find(_inventory(containers=remaining), Kind.IMAGE, "example/job:1.0")
    assert job.usage is Usage.UNUSED


def test_a_tag_that_moved_does_not_fool_the_check() -> None:
    # The running container uses image id NGINX. A newer image now carries the tag
    # "nginx:1.27", but the old one must still count as in use: containers point at ids.
    images = [*scenario.images(), ImageInfo("9" * 64, "nginx", "1.27", 1, None)]
    nginx_old = replace(scenario.images()[0], repository="<none>", tag="<none>")
    images[0] = nginx_old
    inv = _inventory(images=images)
    old = _find(inv, Kind.IMAGE, "<untagged> " + scenario.NGINX[:12])
    assert old.usage is Usage.IN_USE


def test_an_unused_tagged_image_is_offered_but_not_ticked() -> None:
    alpine = _find(_inventory(), Kind.IMAGE, "alpine:3.19")
    assert alpine.usage is Usage.UNUSED
    assert not alpine.preselected


def test_an_untagged_image_is_unused_and_ticked_and_removed_by_id() -> None:
    untagged = _find(_inventory(), Kind.IMAGE, "<untagged>")
    assert untagged.usage is Usage.UNUSED
    assert untagged.preselected
    assert untagged.remove_args == (("rmi", scenario.UNTAGGED),)


def test_an_image_with_two_tags_is_one_resource_removed_tag_by_tag() -> None:
    inv = _inventory()
    app = _find(inv, Kind.IMAGE, "example/app")
    assert app.name == "example/app:latest, example/app:old"
    assert app.remove_args == (("rmi", "example/app:latest"), ("rmi", "example/app:old"))
    assert len(inv.of_kind(Kind.IMAGE)) == 6  # 7 rows, one image having two of them


def test_a_repository_without_a_tag_is_treated_as_untagged() -> None:
    images = [ImageInfo("7" * 64, "example/pinned", "<none>", 1, None)]
    (image,) = _inventory(images=images).of_kind(Kind.IMAGE)
    assert image.remove_args == (("rmi", "7" * 64),)


# --- volumes ------------------------------------------------------------------------


def test_a_mounted_volume_is_in_use_whatever_state_the_container_is_in() -> None:
    inv = _inventory()
    assert _find(inv, Kind.VOLUME, "web-data").usage is Usage.IN_USE
    # old-job is stopped, but its volume still belongs to it.
    assert _find(inv, Kind.VOLUME, scenario.ANON_USED[:12]).usage is Usage.IN_USE


def test_an_unmounted_volume_is_unused_never_ticked_and_warns_about_data() -> None:
    orphan = _find(_inventory(), Kind.VOLUME, "orphan-data")
    assert orphan.usage is Usage.UNUSED
    assert not orphan.preselected
    assert "deletes its data" in orphan.note
    assert orphan.size == 1_200_000_000


def test_anonymous_volumes_are_labelled_and_a_missing_size_is_unknown() -> None:
    inv = _inventory(volume_sizes={})
    free = _find(inv, Kind.VOLUME, scenario.ANON_FREE[:12])
    assert free.name.endswith("(anonymous)")
    assert free.size is None


def test_a_volume_never_gets_ticked_even_if_it_is_unused_and_anonymous() -> None:
    assert not any(r.preselected for r in _inventory().of_kind(Kind.VOLUME))


# --- networks -----------------------------------------------------------------------


def test_built_in_networks_are_protected() -> None:
    inv = _inventory()
    for name in ("bridge", "host", "none"):
        assert _find(inv, Kind.NETWORK, name).usage is Usage.PROTECTED


def test_a_network_with_a_container_is_in_use_and_an_empty_one_is_unused() -> None:
    inv = _inventory()
    assert _find(inv, Kind.NETWORK, "app-net").usage is Usage.IN_USE
    scratch = _find(inv, Kind.NETWORK, "scratch-net")
    assert scratch.usage is Usage.UNUSED
    assert scratch.remove_args == (("network", "rm", scenario.N_SCRATCH),)


def test_a_network_used_only_by_a_stopped_container_still_counts_as_used() -> None:
    box: ContainerInfo = replace(scenario.containers()[2], networks=("scratch-net",))
    inv = _inventory(containers=[box])
    assert _find(inv, Kind.NETWORK, "scratch-net").usage is Usage.IN_USE


# --- the inventory ------------------------------------------------------------------


def test_each_kind_lists_unused_first_then_by_size() -> None:
    images = _inventory().of_kind(Kind.IMAGE)
    unused = [r for r in images if r.removable]
    assert images[: len(unused)] == tuple(unused)
    sizes = [r.size or 0 for r in unused]
    assert sizes == sorted(sizes, reverse=True)


def test_reclaimable_adds_up_only_what_is_unused() -> None:
    inv = _inventory()
    # old-job 2048 + alpine 7.4M + untagged 55M + app 300M + orphan 1.2G + anon 64000
    assert inv.reclaimable() == 2048 + 7_400_000 + 55_000_000 + 300_000_000 + 1_200_000_000 + 64_000
    assert inv.reclaimable(Kind.NETWORK) == 0


def test_an_empty_host_gives_an_empty_inventory() -> None:
    inv = build_inventory([], [], [], [], {})
    assert inv.resources == ()


# --- the cleanup plan ---------------------------------------------------------------


def test_the_plan_lists_the_exact_commands() -> None:
    inv = _inventory()
    chosen = [_find(inv, Kind.CONTAINER, "old-job"), _find(inv, Kind.VOLUME, "orphan-data")]
    plan = plan_cleanup(chosen)
    assert [p.text for p in plan] == [
        f"docker rm {scenario.C_JOB}",
        "docker volume rm orphan-data",
    ]


def test_the_plan_removes_containers_before_images_and_networks_last() -> None:
    inv = _inventory()
    chosen = [
        _find(inv, Kind.NETWORK, "scratch-net"),
        _find(inv, Kind.IMAGE, "alpine"),
        _find(inv, Kind.VOLUME, "orphan-data"),
        _find(inv, Kind.CONTAINER, "old-job"),
    ]
    assert [p.resource.kind for p in plan_cleanup(chosen)] == [
        Kind.CONTAINER,
        Kind.IMAGE,
        Kind.VOLUME,
        Kind.NETWORK,
    ]


def test_a_two_tag_image_gives_two_commands() -> None:
    plan = plan_cleanup([_find(_inventory(), Kind.IMAGE, "example/app")])
    assert [p.text for p in plan] == ["docker rmi example/app:latest", "docker rmi example/app:old"]


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        (Kind.CONTAINER, "web-1"),
        (Kind.IMAGE, "nginx:1.27"),
        (Kind.VOLUME, "web-data"),
        (Kind.NETWORK, "app-net"),
        (Kind.NETWORK, "bridge"),
    ],
)
def test_the_plan_refuses_anything_in_use_or_protected(kind: Kind, name: str) -> None:
    with pytest.raises(NotRemovableError, match=name):
        plan_cleanup([_find(_inventory(), kind, name)])


def test_one_blocked_item_blocks_the_whole_plan() -> None:
    inv = _inventory()
    with pytest.raises(NotRemovableError):
        plan_cleanup([_find(inv, Kind.IMAGE, "alpine"), _find(inv, Kind.VOLUME, "web-data")])


def test_no_command_ever_forces_or_prunes() -> None:
    inv = _inventory()
    every = plan_cleanup(r for r in inv.resources if r.removable)
    assert every
    for planned in every:
        assert not {"-f", "--force", "prune"} & set(planned.args)
        assert "system" not in planned.args


def test_names_with_spaces_or_shell_characters_stay_a_single_argument() -> None:
    volume = VolumeInfo("odd name; rm -rf /", "local")
    inv = build_inventory([], [], [volume], [], {})
    (planned,) = plan_cleanup(inv.resources)
    assert planned.args == ("volume", "rm", "odd name; rm -rf /")
    assert planned.text == "docker volume rm 'odd name; rm -rf /'"
