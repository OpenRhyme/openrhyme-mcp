from datetime import UTC
from zoneinfo import ZoneInfo

import pytest

from openrhyme_mcp.timespec import TimeSpecError, parse

NOW = 1_756_710_000.0  # 2025-09-01T07:00:00Z


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30s", NOW - 30),
        ("30m", NOW - 1800),
        ("2h", NOW - 7200),
        ("1d", NOW - 86400),
        ("1.5h", NOW - 5400),
        ("1756700000", 1_756_700_000.0),
        ("1756700000.5", 1_756_700_000.5),
        ("2025-09-01T07:00:00Z", NOW),
        ("2025-09-01T07:00:00.250Z", NOW + 0.25),
        ("2025-09-01T09:00:00+02:00", NOW),
        ("2025-09-01T07:00:00", NOW),  # local == UTC in this test
        ("2025-09-01 07:00", NOW),
        ("2025-09-01", 1_756_684_800.0),
    ],
)
def test_parses(text: str, expected: float) -> None:
    assert parse(text, now=NOW, tz=UTC) == pytest.approx(expected, abs=1e-3)


@pytest.mark.parametrize("text", ["", "yesterday", "2h30m", "1e5", "2025-13-01", "5w"])
def test_rejects(text: str) -> None:
    with pytest.raises(TimeSpecError):
        parse(text, now=NOW, tz=UTC)


def test_local_time_uses_given_zone() -> None:
    assert parse("2025-09-01T16:00:00", now=NOW, tz=ZoneInfo("Asia/Tokyo")) == NOW
