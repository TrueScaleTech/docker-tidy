from docker_tidy.models import KIND_ORDER, Inventory, Kind, Resource, Usage


def _res(kind: Kind, size: int | None, usage: Usage, key: str = "k") -> Resource:
    return Resource(
        kind=kind,
        key=key,
        name=key,
        size=size,
        created=None,
        usage=usage,
        reason="",
        remove_args=(("rm", key),),
    )


def test_only_unused_resources_are_removable() -> None:
    assert _res(Kind.VOLUME, 1, Usage.UNUSED).removable
    assert not _res(Kind.VOLUME, 1, Usage.IN_USE).removable
    assert not _res(Kind.NETWORK, None, Usage.PROTECTED).removable


def test_reclaimable_counts_only_unused_and_treats_unknown_as_zero() -> None:
    inv = Inventory(
        (
            _res(Kind.IMAGE, 100, Usage.UNUSED, "a"),
            _res(Kind.IMAGE, 1000, Usage.IN_USE, "b"),
            _res(Kind.VOLUME, None, Usage.UNUSED, "c"),
            _res(Kind.VOLUME, 5, Usage.UNUSED, "d"),
        )
    )
    assert inv.reclaimable() == 105
    assert inv.reclaimable(Kind.IMAGE) == 100
    assert inv.reclaimable(Kind.NETWORK) == 0


def test_of_kind_filters() -> None:
    inv = Inventory(
        (_res(Kind.IMAGE, 1, Usage.UNUSED, "a"), _res(Kind.VOLUME, 1, Usage.UNUSED, "b"))
    )
    assert [r.key for r in inv.of_kind(Kind.VOLUME)] == ["b"]


def test_containers_are_removed_before_images_and_networks_last() -> None:
    assert KIND_ORDER.index(Kind.CONTAINER) < KIND_ORDER.index(Kind.IMAGE)
    assert KIND_ORDER[-1] is Kind.NETWORK
