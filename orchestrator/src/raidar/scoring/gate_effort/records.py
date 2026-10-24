"""One step's ``guardrail-gate@1`` record, read back from the step's agent logs.

The wrapper (``raidar.subjects.treatments.runtime.guardrail_gate``) writes
``guardrail-gate/guardrail-gate.json`` under the step's agent log dir (the
scorer's ``trace_dir``): every session (role, round, usage, duration) and
every gate run (verdict, error, output log, report dir, duration). This module
reads that record and, per gate run, the blocking findings the gate reported:

1. a machine-readable report in the run's report dir
   (``python-guardrails.gate-report/1``: every finding with its tier), when
   exactly one is there;
2. else the run's output log in ``guardrail-check``'s format: the verdict line
   gives the blocking total, and the listed ``path:line:col: CODE message``
   lines of the ``Blocking`` section give the codes (complete only when every
   blocking finding was listed: the gate caps its listing);
3. else a passing run has no blocking finding by definition, and a refused
   run in an unrecognised format has an unknown count.

Nothing is written. The contract constants are repeated here, not imported
from the component, so the scorer's identity does not move with the wrapper's
runtime code.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GATE_DIR = "guardrail-gate"
RECORD_PATH = f"{GATE_DIR}/guardrail-gate.json"
REPORT_SCHEMA = "python-guardrails.gate-report/1"
BLOCK_TIER = "block"
SIGNAL = "gate_clean_at_step_end"
USAGE_KEYS = {
    "input_tokens": "n_input_tokens",
    "output_tokens": "n_output_tokens",
    "cost_usd": "cost_usd",
}
FIX_ROLE = "fix"

SOURCE_REPORT = "report"
SOURCE_OUTPUT = "output"
SOURCE_VERDICT = "verdict"
SOURCE_UNRECOGNISED = "unrecognised"

_VERDICT_RE = re.compile(r"^guardrail-check: (?:PASSED|REFUSED) \((?P<blocking>\d+) blocking\b")
_SECTION_RE = re.compile(r"^(?P<title>Blocking|Warnings)\b.*\((?P<count>\d+)\):\s*$")
_FINDING_RE = re.compile(r"^\s+\S.*?:\d+:(?:\d+:)? (?P<code>[A-Za-z][A-Za-z0-9._-]*) ")


class GateRecordError(ValueError):
    """The step's gate record exists but cannot be read as ``guardrail-gate@1`` wrote it."""


@dataclass(frozen=True)
class RunFindings:
    """The blocking findings of one gate run, as far as the stored files tell."""

    source: str
    total: int | None
    codes: tuple[str, ...] | None
    """Every blocking finding's code, or None when the stored files do not list them all."""
    listed: int = 0

    @property
    def complete(self) -> bool:
        return self.codes is not None


@dataclass(frozen=True)
class GateRunView:
    run: int
    after: str
    passed: bool
    error: str | None
    timed_out: bool
    log: str | None
    findings: RunFindings
    duration_s: float | None

    @property
    def decided(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class SessionView:
    name: str
    role: str
    round: int
    error: str | None
    usage: dict[str, float | None]
    duration_s: float | None


@dataclass(frozen=True)
class StepGate:
    termination_reason: str
    error: str | None
    clean_at_end: bool | None
    runs: tuple[GateRunView, ...]
    sessions: tuple[SessionView, ...]

    @property
    def fix_sessions(self) -> tuple[SessionView, ...]:
        return tuple(session for session in self.sessions if session.role == FIX_ROLE)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _inside(trace_dir: Path, relative: Any) -> Path | None:
    """``trace_dir / relative`` when it names a path inside the trace dir, else None."""
    if not isinstance(relative, str) or not relative:
        return None
    path = (trace_dir / relative).resolve()
    return path if path.is_relative_to(trace_dir.resolve()) else None


def _reports(folder: Path) -> list[dict[str, Any]]:
    """The gate reports (``REPORT_SCHEMA``) in a run's report dir."""
    reports = []
    for path in sorted(folder.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("schema") == REPORT_SCHEMA:
            reports.append(payload)
    return reports


def _report_findings(trace_dir: Path, report_dir: Any) -> RunFindings | None:
    folder = _inside(trace_dir, report_dir)
    reports = _reports(folder) if folder is not None and folder.is_dir() else []
    if len(reports) != 1 or not isinstance(reports[0].get("findings"), list):
        return None
    codes = tuple(
        str(item.get("code"))
        for item in reports[0]["findings"]
        if isinstance(item, dict) and item.get("tier") == BLOCK_TIER
    )
    return RunFindings(SOURCE_REPORT, len(codes), codes, listed=len(codes))


def parse_output(output: str) -> RunFindings | None:
    """Blocking findings from ``guardrail-check``'s printed output, if it is that format."""
    total: int | None = None
    section: str | None = None
    codes: list[str] = []
    for line in output.splitlines():
        if total is None and (verdict := _VERDICT_RE.match(line)):
            total = int(verdict["blocking"])
        elif heading := _SECTION_RE.match(line):
            section = heading["title"]
        elif section == "Blocking" and (finding := _FINDING_RE.match(line)):
            codes.append(finding["code"])
        elif not line.startswith(" "):
            section = None
    if total is None:
        return None
    complete = len(codes) == total
    return RunFindings(SOURCE_OUTPUT, total, tuple(codes) if complete else None, listed=len(codes))


def _log_findings(trace_dir: Path, log: Any) -> RunFindings | None:
    path = _inside(trace_dir, log)
    if path is None or not path.is_file():
        return None
    return parse_output(path.read_text(encoding="utf-8", errors="replace"))


def run_findings(trace_dir: Path, run: dict[str, Any]) -> RunFindings:
    """The run's blocking findings from its report, else its output, else its verdict."""
    found = _report_findings(trace_dir, run.get("report_dir")) or _log_findings(
        trace_dir, run.get("log")
    )
    if found is not None:
        return found
    if run.get("passed") is True:
        return RunFindings(SOURCE_VERDICT, 0, (), listed=0)
    return RunFindings(SOURCE_UNRECOGNISED, None, None)


def _run_view(trace_dir: Path, run: Any) -> GateRunView:
    if not isinstance(run, dict):
        raise GateRecordError("a gate run is not a JSON object")
    return GateRunView(
        run=_int(run.get("run")),
        after=str(run.get("after") or ""),
        passed=run.get("passed") is True,
        error=str(run["error"]) if run.get("error") else None,
        timed_out=run.get("timed_out") is True,
        log=run.get("log") if isinstance(run.get("log"), str) else None,
        findings=run_findings(trace_dir, run),
        duration_s=_number(run.get("duration_sec")),
    )


def _session_view(stage: Any) -> SessionView:
    if not isinstance(stage, dict):
        raise GateRecordError("a session is not a JSON object")
    index, role = _int(stage.get("index")), str(stage.get("stage") or "")
    return SessionView(
        name=f"stage_{index}_{role}",
        role=role,
        round=_int(stage.get("round")),
        error=str(stage["error"]) if stage.get("error") else None,
        usage={metric: _number(stage.get(key)) for metric, key in USAGE_KEYS.items()},
        duration_s=_number(stage.get("duration_sec")),
    )


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateRecordError(f"{RECORD_PATH} is not readable JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise GateRecordError(f"{RECORD_PATH} is not a JSON object")
    for key in ("termination_reason", "gate_runs", "stages"):
        if key not in payload:
            raise GateRecordError(f"{RECORD_PATH} has no {key!r}")
    if not isinstance(payload["gate_runs"], list) or not isinstance(payload["stages"], list):
        raise GateRecordError(f"{RECORD_PATH}: gate_runs and stages must be lists")
    return payload


def has_record(trace_dir: Path | None) -> bool:
    return trace_dir is not None and (trace_dir / RECORD_PATH).is_file()


def read_step_gate(trace_dir: Path | None) -> StepGate | None:
    """The step's gate record, or None when the step ran without guardrail-gate@1."""
    if trace_dir is None or not has_record(trace_dir):
        return None
    payload = _load(trace_dir / RECORD_PATH)
    signal = payload.get(SIGNAL)
    return StepGate(
        termination_reason=str(payload["termination_reason"]),
        error=str(payload["error"]) if payload.get("error") else None,
        clean_at_end=signal if isinstance(signal, bool) else None,
        runs=tuple(_run_view(trace_dir, run) for run in payload["gate_runs"]),
        sessions=tuple(_session_view(stage) for stage in payload["stages"]),
    )
