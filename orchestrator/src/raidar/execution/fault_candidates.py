"""Provisional fault occurrences from run-health (``<job>/fault-candidates.jsonl``).

Every health ``violation`` and every idle finding (``idle_observed`` in observe mode,
``idle_killed`` in enforce mode) appends one
line ``{trial, step?, check_id, status, detail, evidence, state: "provisional"}``. They are
candidates only: the ledger reconciles them into fault categories later. Appending is
idempotent per ``(trial, step, check_id, status)``, so re-running ``raidar health`` on a
job never duplicates a line.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from raidar.execution.health_model import VIOLATION, HealthCheck, HealthReport

FAULT_CANDIDATES = "fault-candidates.jsonl"
IDLE_OBSERVED = "idle_observed"
IDLE_KILLED = "idle_killed"
IDLE_DECISIONS = (IDLE_OBSERVED, IDLE_KILLED)


def _steps(check: HealthCheck) -> list[str | None]:
    steps = check.evidence.get("steps")
    return [str(step) for step in steps] if steps else [None]


def _idle_findings(check: HealthCheck) -> list[dict[str, Any]]:
    findings = check.evidence.get("findings") or []
    return [f for f in findings if f.get("decision") in IDLE_DECISIONS]


def _reason(finding: dict[str, Any]) -> str:
    reason = str(finding.get("reason"))
    if finding.get("decision") != IDLE_KILLED:
        return reason
    return f"{reason}; killed pids {finding.get('killed_pids')}"


def _candidate(
    trial: str, step: str | None, check: HealthCheck, status: str, detail: str | None = None
) -> dict[str, Any]:
    line: dict[str, Any] = {"trial": trial}
    if step is not None:
        line["step"] = step
    line |= {
        "check_id": check.id,
        "status": status,
        "detail": check.detail if detail is None else detail,
        "evidence": check.evidence,
        "state": "provisional",
    }
    return line


def candidates(report: HealthReport) -> list[dict[str, Any]]:
    """The provisional occurrences of one trial's report, in check order."""
    lines: list[dict[str, Any]] = []
    for check in report.checks:
        if check.status == VIOLATION:
            lines += [_candidate(report.trial_id, s, check, VIOLATION) for s in _steps(check)]
        lines += [
            _candidate(report.trial_id, str(f.get("step")), check, str(f["decision"]), _reason(f))
            for f in _idle_findings(check)
        ]
    return lines


def _key(line: dict[str, Any]) -> tuple[Any, ...]:
    return (line.get("trial"), line.get("step"), line.get("check_id"), line.get("status"))


def _existing(path: Path) -> set[tuple[Any, ...]]:
    if not path.is_file():
        return set()
    keys = set()
    for raw in path.read_text().splitlines():
        try:
            keys.add(_key(json.loads(raw)))
        except ValueError:
            continue
    return keys


def append_fault_candidates(job_dir: Path, report: HealthReport) -> list[dict[str, Any]]:
    """Append the report's new candidate lines to the job's file; returns the lines written."""
    path = job_dir / FAULT_CANDIDATES
    seen = _existing(path)
    fresh = [line for line in candidates(report) if _key(line) not in seen]
    if fresh:
        with path.open("a") as handle:
            for line in fresh:
                handle.write(json.dumps(line, sort_keys=True) + "\n")
    return fresh
