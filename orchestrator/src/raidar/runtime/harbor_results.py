"""Harbor result artifact parsing helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from raidar.runtime.harbor_env import _redact_sensitive_text

REGISTRY_RATE_LIMIT_PATTERN = re.compile(
    r"(?:toomanyrequests|too many requests|pull rate limit|rate limit exceeded|429)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class TrialFailure:
    """Typed terminal failure extracted from Harbor trial artifacts."""

    reason: str
    code: str


def _is_registry_rate_limited(run_harbor_dir: Path) -> bool:
    for name in ("harbor-stdout.log", "harbor-stderr.log"):
        log_path = run_harbor_dir / name
        if not log_path.exists():
            continue
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        if REGISTRY_RATE_LIMIT_PATTERN.search(log_text):
            return True
    return False


def detect_trial_failure(trial_dir: Path | None) -> TrialFailure | None:
    """Extract a terminal failure reason from Harbor trial artifacts."""
    if not trial_dir:
        return None
    return _trial_exception_failure(trial_dir) or _codex_turn_failure(trial_dir)


def _trial_exception_failure(trial_dir: Path) -> TrialFailure | None:
    result_data = _load_json_dict(trial_dir / "result.json")
    exception_info = result_data.get("exception_info")
    if not isinstance(exception_info, dict):
        return None
    message = exception_info.get("exception_message")
    if not isinstance(message, str):
        return None
    message = message.strip()
    if not message:
        return None
    return TrialFailure(
        reason=f"Harbor trial exception: {_redact_sensitive_text(message)}",
        code="harbor_trial_exception",
    )


def _codex_turn_failure(trial_dir: Path) -> TrialFailure | None:
    codex_log = trial_dir / "agent" / "codex.txt"
    if not codex_log.exists():
        return None
    for line in reversed(codex_log.read_text(errors="ignore").splitlines()):
        if '"type":"turn.failed"' not in line:
            continue
        message = _codex_turn_failure_message(line)
        reason = f"Codex turn failed: {message}" if message else "Codex turn failed."
        return TrialFailure(reason=reason, code=_codex_turn_failure_code(message))
    return None


def _codex_turn_failure_code(message: str | None) -> str:
    normalized = (message or "").lower()
    if "rate limit" in normalized or "429" in normalized:
        return "provider_rate_limit"
    if "stream disconnected before completion" in normalized:
        return "provider_stream_disconnect"
    return "provider_or_harness_turn_failure"


def _codex_turn_failure_message(line: str) -> str | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        message: str | None = line
    else:
        raw_message = payload.get("error", {}).get("message")
        message = raw_message if isinstance(raw_message, str) else None
    if not message:
        return None
    message = message.strip()
    return _redact_sensitive_text(message) if message else None


def _load_json_dict(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_iso8601_timestamp(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _duration_seconds(start: str | None, end: str | None) -> float | None:
    start_ts = _parse_iso8601_timestamp(start)
    end_ts = _parse_iso8601_timestamp(end)
    if not start_ts or not end_ts:
        return None
    duration = (end_ts - start_ts).total_seconds()
    return round(max(0.0, duration), 3)


def _phase_duration(payload: dict, phase_key: str) -> float | None:
    phase_data = payload.get(phase_key)
    if not isinstance(phase_data, dict):
        return None
    return _duration_seconds(phase_data.get("started_at"), phase_data.get("finished_at"))


def _harbor_phase_timings(trial_dir: Path | None) -> dict[str, float]:
    if not trial_dir:
        return {}
    payload = _load_json_dict(trial_dir / "result.json")
    if not payload:
        return {}

    timings = {
        "trial_total_sec": _duration_seconds(payload.get("started_at"), payload.get("finished_at")),
        "environment_setup_sec": _phase_duration(payload, "environment_setup"),
        "harness_setup_sec": _phase_duration(payload, "agent_setup"),
        "harness_execution_sec": _phase_duration(payload, "agent_execution"),
        "verifier_sec": _phase_duration(payload, "verifier"),
    }
    return {key: value for key, value in timings.items() if value is not None}


def _verifier_scorecard_path(trial_dir: Path | None) -> Path | None:
    if not trial_dir:
        return None
    return trial_dir / "verifier" / "scorecard.json"
