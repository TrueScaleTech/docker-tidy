import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from docker_tidy.parse import (
    ParseError,
    normalise_id,
    parse_containers,
    parse_images,
    parse_networks,
    parse_size,
    parse_volume_sizes,
    parse_volumes,
    parse_when,
)

ID_A = "a" * 64
ID_B = "b" * 64


# --- sizes ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0B", 0),
        ("512B", 512),
        ("78.1MB", 78_100_000),
        ("1.2kB", 1200),
        ("2GB", 2_000_000_000),
        ("1.5GiB", 1_610_612_736),
        ("3tb", 3_000_000_000_000),
        ("  4MB  ", 4_000_000),
    ],
)
def test_parse_size_reads_docker_units(text: str, expected: int) -> None:
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["N/A", "", "big", "12", "MB", "1.2 parsecs", "-3MB"])
def test_parse_size_gives_none_for_what_it_cannot_read(text: str) -> None:
    assert parse_size(text) is None


# --- dates ------------------------------------------------------------------------


def test_parse_when_reads_the_list_style() -> None:
    assert parse_when("2024-03-05 09:30:00 +0000 UTC") == datetime(2024, 3, 5, 9, 30, tzinfo=UTC)


def test_parse_when_keeps_the_offset() -> None:
    got = parse_when("2024-03-05 09:30:00 +0200 CEST")
    assert got == datetime(2024, 3, 5, 9, 30, tzinfo=timezone(timedelta(hours=2)))
    assert got is not None
    assert got.utcoffset() == timedelta(hours=2)


def test_parse_when_reads_the_inspect_style_with_fraction() -> None:
    assert parse_when("2024-03-05T09:30:00.123456789Z") == datetime(2024, 3, 5, 9, 30, tzinfo=UTC)


def test_parse_when_gives_none_for_nonsense() -> None:
    assert parse_when("") is None
    assert parse_when("yesterday") is None
    assert parse_when("2024-13-45 99:99:99") is None


def test_ids_compare_with_and_without_prefix() -> None:
    assert normalise_id("sha256:" + ID_A) == normalise_id(ID_A)


# --- json rows --------------------------------------------------------------------


def _lines(*rows: dict[str, object]) -> str:
    return "\n".join(json.dumps(r) for r in rows) + "\n"


def test_images_are_read_from_json_lines() -> None:
    text = _lines(
        {
            "ID": "sha256:" + ID_A,
            "Repository": "example/app",
            "Tag": "1.0",
            "Size": "78.1MB",
            "CreatedAt": "2024-03-05 09:30:00 +0000 UTC",
            "Containers": "N/A",
        },
        {"ID": "sha256:" + ID_B, "Repository": "<none>", "Tag": "<none>", "Size": "5MB"},
    )
    first, second = parse_images(text)
    assert (first.id, first.repository, first.tag, first.size) == (
        ID_A,
        "example/app",
        "1.0",
        78_100_000,
    )
    assert first.created == datetime(2024, 3, 5, 9, 30, tzinfo=UTC)
    assert (second.repository, second.tag, second.created) == ("<none>", "<none>", None)


def test_a_json_list_is_accepted_too() -> None:
    text = json.dumps([{"Name": "data", "Driver": "local"}])
    assert [(v.name, v.driver) for v in parse_volumes(text)] == [("data", "local")]


def test_empty_output_is_an_empty_list() -> None:
    assert parse_images("") == []
    assert parse_volumes("\n\n") == []


def test_output_that_is_not_json_is_a_parse_error() -> None:
    with pytest.raises(ParseError, match="not JSON"):
        parse_images("this is a table\nNOT json\n")


def test_json_that_is_not_objects_is_a_parse_error() -> None:
    with pytest.raises(ParseError, match="list of objects"):
        parse_images("[1, 2, 3]")


def test_volumes_without_a_name_are_ignored() -> None:
    text = _lines({"Name": "", "Driver": "local"}, {"Name": "keep", "Driver": "local"})
    assert [v.name for v in parse_volumes(text)] == ["keep"]


def test_networks_are_read() -> None:
    text = _lines(
        {
            "ID": ID_A,
            "Name": "app-net",
            "Driver": "bridge",
            "CreatedAt": "2024-03-05 09:30:00.123456789 +0000 UTC",
        }
    )
    (net,) = parse_networks(text)
    assert (net.id, net.name, net.driver) == (ID_A, "app-net", "bridge")
    assert net.created == datetime(2024, 3, 5, 9, 30, tzinfo=UTC)


# --- containers -------------------------------------------------------------------


def _container(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "Id": ID_A,
        "Name": "/web-1",
        "Created": "2024-03-05T09:30:00.5Z",
        "Image": "sha256:" + ID_B,
        "State": {"Status": "running"},
        "SizeRw": 4096,
        "Config": {"Image": "example/app:1.0"},
        "HostConfig": {"NetworkMode": "app-net"},
        "NetworkSettings": {"Networks": {"app-net": {}}},
        "Mounts": [
            {"Type": "volume", "Name": "web-data", "Destination": "/data"},
            {"Type": "bind", "Source": "/srv/site", "Destination": "/site"},
        ],
    }
    return base | overrides


def test_container_fields_are_read() -> None:
    (c,) = parse_containers(json.dumps([_container()]))
    assert c.id == ID_A
    assert c.name == "web-1"
    assert c.image_id == ID_B
    assert c.image_name == "example/app:1.0"
    assert c.state == "running"
    assert c.size_rw == 4096
    assert c.volumes == ("web-data",)  # bind mounts are not volumes
    assert c.networks == ("app-net",)
    assert c.created == datetime(2024, 3, 5, 9, 30, tzinfo=UTC)


def test_default_network_mode_means_bridge() -> None:
    row = _container(HostConfig={"NetworkMode": "default"}, NetworkSettings={"Networks": {}})
    (c,) = parse_containers(json.dumps([row]))
    assert c.networks == ("bridge",)


def test_a_container_sharing_another_containers_network_adds_no_network_of_its_own() -> None:
    row = _container(
        HostConfig={"NetworkMode": "container:" + ID_B}, NetworkSettings={"Networks": {}}
    )
    (c,) = parse_containers(json.dumps([row]))
    assert c.networks == ()


def test_networks_from_settings_and_mode_are_merged_without_duplicates() -> None:
    row = _container(
        HostConfig={"NetworkMode": "app-net"},
        NetworkSettings={"Networks": {"app-net": {}, "db-net": {}}},
    )
    (c,) = parse_containers(json.dumps([row]))
    assert c.networks == ("app-net", "db-net")


def test_missing_fields_become_unknown_not_errors() -> None:
    (c,) = parse_containers(json.dumps([{"Id": ID_A}]))
    assert (c.name, c.state, c.size_rw, c.created) == ("", "unknown", None, None)
    assert c.volumes == () and c.networks == ()


def test_size_that_is_not_a_number_is_unknown() -> None:
    (c,) = parse_containers(json.dumps([_container(SizeRw="big")]))
    assert c.size_rw is None


# --- volume sizes from `docker system df -v` --------------------------------------

DF_VERBOSE = """\
Images space usage:

REPOSITORY    TAG   IMAGE ID       CREATED      SIZE     SHARED SIZE   UNIQUE SIZE   CONTAINERS
example/app   1.0   aaaaaaaaaaaa   2 days ago   78.1MB   0B            78.1MB        1

Containers space usage:

CONTAINER ID   IMAGE         COMMAND   LOCAL VOLUMES   SIZE      CREATED       STATUS
aaaaaaaaaaaa   example/app   "run"     1               0B        2 days ago    Up 2 days

Local Volumes space usage:

VOLUME NAME                 LINKS     SIZE
web-data                    1         12.5MB
orphan-data                 0         1.2GB
empty-volume                0         0B

Build cache usage: 0B

CACHE ID   CACHE TYPE   SIZE   CREATED   LAST USED   USAGE   SHARED
"""


def test_volume_sizes_come_from_the_volumes_table_only() -> None:
    assert parse_volume_sizes(DF_VERBOSE) == {
        "web-data": 12_500_000,
        "orphan-data": 1_200_000_000,
        "empty-volume": 0,
    }


def test_volume_sizes_are_empty_when_the_table_is_missing_or_empty() -> None:
    assert parse_volume_sizes("") == {}
    assert parse_volume_sizes("Local Volumes space usage:\n\nVOLUME NAME   LINKS   SIZE\n\n") == {}
    assert parse_volume_sizes("nothing useful here\n") == {}


def test_a_volume_row_with_an_unreadable_size_is_skipped() -> None:
    text = "VOLUME NAME   LINKS   SIZE\nweird   0   N/A\nfine   0   3MB\n"
    assert parse_volume_sizes(text) == {"fine": 3_000_000}
