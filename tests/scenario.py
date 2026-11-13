"""A small, invented Docker host used by several test files.

Every name, id and size here is made up. The host runs a web server and a database
(both with their data and network), has one finished job, and has accumulated some
leftovers: an unused image with two tags, an untagged image, an unused volume, an
anonymous volume, and an unused network.
"""

from __future__ import annotations

from docker_tidy.models import ContainerInfo, ImageInfo, NetworkInfo, VolumeInfo
from docker_tidy.parse import parse_when

NGINX = "1" * 64
POSTGRES = "2" * 64
JOB = "3" * 64
ALPINE = "4" * 64
UNTAGGED = "5" * 64
APP = "6" * 64

C_WEB = "a1" * 32
C_DB = "a2" * 32
C_JOB = "a3" * 32

ANON_USED = "c1" * 32
ANON_FREE = "c2" * 32

N_APP = "d1" * 32
N_SCRATCH = "d2" * 32
N_BRIDGE = "d3" * 32
N_HOST = "d4" * 32
N_NONE = "d5" * 32

WHEN = parse_when("2024-03-05 09:30:00 +0000 UTC")


def containers() -> list[ContainerInfo]:
    return [
        ContainerInfo(C_WEB, "web-1", NGINX, "nginx:1.27", "running", 0, WHEN,
                      ("web-data",), ("app-net",)),
        ContainerInfo(C_DB, "db-1", POSTGRES, "postgres:16", "running", 4096, WHEN,
                      ("db-data",), ("app-net",)),
        ContainerInfo(C_JOB, "old-job", JOB, "example/job:1.0", "exited", 2048, WHEN,
                      (ANON_USED,), ("bridge",)),
    ]  # fmt: skip


def images() -> list[ImageInfo]:
    return [
        ImageInfo(NGINX, "nginx", "1.27", 190_000_000, WHEN),
        ImageInfo(POSTGRES, "postgres", "16", 430_000_000, WHEN),
        ImageInfo(JOB, "example/job", "1.0", 12_000_000, WHEN),
        ImageInfo(ALPINE, "alpine", "3.19", 7_400_000, WHEN),
        ImageInfo(UNTAGGED, "<none>", "<none>", 55_000_000, WHEN),
        ImageInfo(APP, "example/app", "latest", 300_000_000, WHEN),
        ImageInfo(APP, "example/app", "old", 300_000_000, WHEN),
    ]


def volumes() -> list[VolumeInfo]:
    return [
        VolumeInfo("web-data", "local"),
        VolumeInfo("db-data", "local"),
        VolumeInfo("orphan-data", "local"),
        VolumeInfo(ANON_USED, "local"),
        VolumeInfo(ANON_FREE, "local"),
    ]


VOLUME_SIZES = {
    "web-data": 12_500_000,
    "db-data": 800_000_000,
    "orphan-data": 1_200_000_000,
    ANON_USED: 1_000,
    ANON_FREE: 64_000,
}


def networks() -> list[NetworkInfo]:
    return [
        NetworkInfo(N_BRIDGE, "bridge", "bridge", WHEN),
        NetworkInfo(N_HOST, "host", "host", WHEN),
        NetworkInfo(N_NONE, "none", "null", WHEN),
        NetworkInfo(N_APP, "app-net", "bridge", WHEN),
        NetworkInfo(N_SCRATCH, "scratch-net", "bridge", WHEN),
    ]
