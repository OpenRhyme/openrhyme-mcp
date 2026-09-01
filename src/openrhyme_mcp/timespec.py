"""The `<time>` grammar shared with the engine CLI (engine spec §9).

Relative durations (`30s`, `2h`, `1d`, decimals allowed) mean "that long ago"; otherwise
unix seconds, ISO-8601 with a zone, or a local date/time without a zone.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, tzinfo

_RELATIVE = re.compile(r"^(\d+(?:\.\d+)?)([smhd])$")
_UNIX = re.compile(r"^\d+(?:\.\d+)?$")
_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
_LOCAL_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


class TimeSpecError(ValueError):
    """Raised for text that matches none of the accepted forms."""


def parse(text: str, *, now: float | None = None, tz: tzinfo | None = None) -> float:
    """Return unix seconds for `text`. `now` and `tz` exist for deterministic tests."""
    s = text.strip()
    if not s:
        raise TimeSpecError(text)

    relative = _RELATIVE.match(s)
    if relative:
        base = now if now is not None else datetime.now(UTC).timestamp()
        return base - float(relative.group(1)) * _UNITS[relative.group(2)]

    if _UNIX.match(s):
        return float(s)

    try:
        aware = datetime.fromisoformat(s)
    except ValueError:
        aware = None
    if aware is not None and aware.tzinfo is not None:
        return aware.timestamp()

    local_zone = tz or datetime.now().astimezone().tzinfo
    for fmt in _LOCAL_FORMATS:
        try:
            naive = datetime.strptime(s, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=local_zone).timestamp()

    raise TimeSpecError(text)
