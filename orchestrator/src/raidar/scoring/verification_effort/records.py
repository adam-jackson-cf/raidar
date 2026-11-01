"""One step's ``gate/1`` record, read back from the step's host-written ``workflow.json``.

``PackageCheckpoint.record`` stores the gate under the key ``gate/1`` of the step's workflow
record (``<trace_dir>/workflow/workflow.json``, published after the stages closed):

- ``max_fix_rounds`` and ``errors``;
- ``gates``: one seal per agent turn, each with ``run``, ``after``, ``verdict``
  (``passed``, ``refused`` or ``error``), ``attribution``, ``seconds``, ``error`` and ``checks``
  (``name``, ``category``, ``blocking``, ``status`` ``passed``/``failed``/``error``, ``cause``);
- ``pre_fix_snapshots``: ``round``, ``path`` (relative to the trace dir) and ``tree_digest`` of
  the workspace each fix turn started from.

The fix turns are the workflow record's ``turns`` of kind ``gate_fix``: their tokens are each
turn's own share. A step whose workflow ran no ``gate/1`` package has no ``gate/1`` key (or no
workflow record) and reads as ``None``; a record that exists but cannot be read is an error, never
an empty gate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WORKFLOW_RECORD = "workflow/workflow.json"
GATE_KEY = "gate/1"
GATE_FIX = "gate_fix"
PASSED = "passed"
REFUSED = "refused"
FAILED = "failed"
VERDICTS = frozenset({PASSED, REFUSED, "error"})
CHECK_STATUSES = frozenset({PASSED, FAILED, "error"})


class GateRecordError(ValueError):
    """The step's workflow record exists but its ``gate/1`` record cannot be read."""


@dataclass(frozen=True)
class CheckView:
    name: str
    kind: str | None
    blocking: bool
    status: str
    cause: str | None
    seconds: float | None

    @property
    def failed_blocking(self) -> bool:
        return self.blocking and self.status == FAILED


@dataclass(frozen=True)
class GateRunView:
    run: int
    after: str | None
    verdict: str
    attribution: str | None
    seconds: float | None
    error: str | None
    checks: tuple[CheckView, ...]

    @property
    def decided(self) -> bool:
        return self.verdict in (PASSED, REFUSED)

    @property
    def passed(self) -> bool:
        return self.verdict == PASSED

    @property
    def blocking_failures(self) -> tuple[CheckView, ...]:
        return tuple(check for check in self.checks if check.failed_blocking)


@dataclass(frozen=True)
class FixTurn:
    index: int
    seconds: float | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None


@dataclass(frozen=True)
class PreFix:
    round: int
    path: str
    tree_digest: str | None


@dataclass(frozen=True)
class StepGate:
    max_fix_rounds: int
    runs: tuple[GateRunView, ...]
    fixes: tuple[FixTurn, ...]
    pre_fix: tuple[PreFix, ...]
    errors: tuple[str, ...]

    @property
    def last(self) -> GateRunView | None:
        return self.runs[-1] if self.runs else None

    @property
    def clean_at_end(self) -> bool | None:
        """Whether the last seal passed; ``None`` when there is none or it could not decide."""
        last = self.last
        return None if last is None or not last.decided else last.passed


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _count(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _check(item: Any) -> CheckView:
    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
        raise GateRecordError("a gate check has no name")
    status = item.get("status")
    if status not in CHECK_STATUSES:
        raise GateRecordError(f"gate check {item['name']!r} has status {status!r}")
    if not isinstance(item.get("blocking"), bool):
        raise GateRecordError(f"gate check {item['name']!r} has no boolean blocking flag")
    return CheckView(
        item["name"],
        _text(item.get("category")),
        item["blocking"],
        status,
        _text(item.get("cause")),
        _number(item.get("seconds")),
    )


def _run(item: Any, position: int) -> GateRunView:
    if not isinstance(item, dict) or item.get("verdict") not in VERDICTS:
        raise GateRecordError(f"gate seal {position} has no valid verdict")
    checks = item.get("checks")
    if not isinstance(checks, list):
        raise GateRecordError(f"gate seal {position} has no checks list")
    number = _count(item.get("run"))
    return GateRunView(
        position if number is None else number,
        _text(item.get("after")),
        item["verdict"],
        _text(item.get("attribution")),
        _number(item.get("seconds")),
        _text(item.get("error")),
        tuple(_check(check) for check in checks),
    )


def _pre_fix(item: Any) -> PreFix:
    path = item.get("path") if isinstance(item, dict) else None
    if not isinstance(path, str):
        raise GateRecordError("a pre-fix snapshot entry has no path")
    digest = item.get("tree_digest")
    return PreFix(_count(item.get("round")) or 0, path, digest if isinstance(digest, str) else None)


def _fixes(record: dict[str, Any]) -> tuple[FixTurn, ...]:
    turns = record.get("turns")
    if not isinstance(turns, list):
        raise GateRecordError("the workflow record has no turns list")
    fixes: list[FixTurn] = []
    for turn in turns:
        if not isinstance(turn, dict) or turn.get("kind") != GATE_FIX:
            continue
        raw_tokens = turn.get("tokens")
        tokens = raw_tokens if isinstance(raw_tokens, dict) else {}
        fixes.append(
            FixTurn(
                _count(turn.get("turn")) or len(fixes) + 1,
                _number(turn.get("seconds")),
                _count(tokens.get("input")),
                _count(tokens.get("output")),
                _number(tokens.get("cost_usd")),
            )
        )
    return tuple(fixes)


def _load(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise GateRecordError(f"{WORKFLOW_RECORD} cannot be read: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GateRecordError(f"{WORKFLOW_RECORD} is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise GateRecordError(f"{WORKFLOW_RECORD} is not an object")
    return payload


def _gate_record(record: dict[str, Any], gate: dict[str, Any]) -> StepGate:
    snapshots = gate.get("pre_fix_snapshots") or []
    errors = gate.get("errors") or []
    if not isinstance(snapshots, list) or not isinstance(errors, list):
        raise GateRecordError(f"the {GATE_KEY} record's snapshots or errors are not lists")
    return StepGate(
        _count(gate.get("max_fix_rounds")) or 0,
        tuple(_run(item, index + 1) for index, item in enumerate(gate["gates"])),
        _fixes(record),
        tuple(_pre_fix(item) for item in snapshots),
        tuple(str(item) for item in errors),
    )


def read_step_gate(trace_dir: Path | None) -> StepGate | None:
    """The step's ``gate/1`` record, or ``None`` when the step ran without a ``gate/1`` package."""
    if trace_dir is None:
        return None
    record = _load(trace_dir / WORKFLOW_RECORD)
    if record is None or GATE_KEY not in record:
        return None
    gate = record[GATE_KEY]
    if not isinstance(gate, dict) or not isinstance(gate.get("gates"), list):
        raise GateRecordError(f"the {GATE_KEY} record has no gates list")
    return _gate_record(record, gate)
