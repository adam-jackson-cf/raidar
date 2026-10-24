"""Harbor's per-trial ``result.json``: reading it and the ``TimingInfo`` arithmetic over it."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

RESULT_FILE = "result.json"


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


def read_result(trial_dir: Path) -> dict[str, Any] | None:
    """Harbor's ``result.json`` of a trial directory, or None before the trial wrote it."""
    path = trial_dir / RESULT_FILE
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text())
    return loaded if isinstance(loaded, dict) else None


def step_results(result: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    """The per-step results of a trial's ``result.json``, by step name."""
    items = (result or {}).get("step_results") or []
    return {item["step_name"]: item for item in items if isinstance(item, dict)}
