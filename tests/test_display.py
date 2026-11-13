from datetime import UTC, datetime, timedelta

import pytest

from docker_tidy.display import format_age, format_size

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (None, "?"),
        (0, "0 B"),
        (999, "999 B"),
        (1000, "1.0 kB"),
        (12_500_000, "12.5 MB"),
        (1_200_000_000, "1.2 GB"),
        (3_400_000_000_000, "3.4 TB"),
        (5_000_000_000_000_000, "5000.0 TB"),
    ],
)
def test_format_size(size: int | None, text: str) -> None:
    assert format_size(size) == text


@pytest.mark.parametrize(
    ("delta", "text"),
    [
        (timedelta(seconds=30), "0m"),
        (timedelta(minutes=5), "5m"),
        (timedelta(hours=3), "3h"),
        (timedelta(days=12), "12d"),
        (timedelta(days=95), "3mo"),
        (timedelta(days=800), "2y"),
        (timedelta(days=-1), "0m"),  # a clock a little ahead of docker's
    ],
)
def test_format_age(delta: timedelta, text: str) -> None:
    assert format_age(NOW - delta, NOW) == text


def test_unknown_age_is_a_question_mark() -> None:
    assert format_age(None, NOW) == "?"
