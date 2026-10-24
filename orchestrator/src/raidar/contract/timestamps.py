"""Harbor ``TimingInfo`` arithmetic over ``result.json`` (ISO timestamps)."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def parse_ts(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) and value else None


def diff_s(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None:
        return None
    return (later - earlier).total_seconds()


def span_s(timing: Any) -> float | None:
    """Duration of a ``{started_at, finished_at}`` span, or None when incomplete."""
    if not isinstance(timing, dict):
        return None
    return diff_s(parse_ts(timing.get("finished_at")), parse_ts(timing.get("started_at")))
