"""Run-health family ``E``: the define stage, over the stage's records.

The checks read only what the workflow runner recorded per attempted step: ``define`` in
``workflow/workflow.json`` (its ending, the ``inputs`` the tool was started with, the draft's
``state``, every ``define`` call in ``calls`` and its ``events`` trace, the final ``definition``
file and its digest) and the verify stage's ending. A trial whose records name no define stage
has no ``E`` checks; a step whose evidence is missing makes a check ``unmeasured``, never
``pass``.

- ``E1`` every checkpoint that ended ``gate_passed`` holds a sealed, passing definition: the
  recorded draft is sealed ``ready``, the define tool's checks pass on it again (without the
  workspace: a ``path:line`` is checked for its form only), the recorded definition is the
  draft's render byte for byte, and its digest is the recorded one;
- ``E2`` the recorded ``define`` calls agree with the final draft: one event per recorded
  call, the last event's state digest is the digest of the recorded state and its seal is the
  state's, and a sealed state is the outcome of a ``done`` call that printed nothing blocking
  (a hand-edited state, call log or definition cannot satisfy all three);
- ``E3`` a checkpoint whose define ended ``definition_unusable`` is recorded as such, holds no
  definition, shows a failing final check, and did not pass the verify gate.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.engine.health.base import Audit, make_check
from raidar.engine.health.model import PASS, UNMEASURED, VIOLATION, HealthCheck
from raidar.subject.workflow.define_tool import tool
from raidar.subject.workflow.define_tool.draft import Draft, DraftError
from raidar.subject.workflow.define_tool.schemas import INTAKE_REPLY_SCHEMA, violations

WORKFLOW_RECORD = "workflow/workflow.json"
READY = "definition_ready"
UNUSABLE = "definition_unusable"
GATE_PASSED = "gate_passed"
_SHOWN = 3


@dataclass(frozen=True)
class StepDefine:
    """One defining step: its agent log directory and the stage and verify records."""

    step: str
    agent: Path
    record: dict[str, Any]
    verify_ending: str | None


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _bytes(agent: Path, record: dict[str, Any], key: str) -> bytes | None:
    relative = record.get(key)
    if not isinstance(relative, str):
        return None
    try:
        return (agent / relative).read_bytes()
    except OSError:
        return None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_HEX = frozenset("0123456789abcdef")
_PATHS = ("inputs", "state", "calls", "events")
_LISTS = ("checks", "repairs", "questions")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _hex(value: Any, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(c in _HEX for c in value)


def _name_problems(record: dict[str, Any]) -> list[str]:
    problems = [f"{key} is not a path" for key in _PATHS if not _text(record.get(key))]
    problems += [f"{key} is not a list" for key in _LISTS if not isinstance(record.get(key), list)]
    if not _text(record.get("ending")):
        problems.append("ending is not a name")
    return problems


def _count_problems(record: dict[str, Any]) -> list[str]:
    problems = []
    if record.get("unit_rules") != 2 or isinstance(record.get("unit_rules"), bool):
        problems.append("unit_rules is not 2")
    rounds = record.get("max_refinement_rounds")
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 0:
        problems.append("max_refinement_rounds is not a count")
    if not _hex(record.get("source_commit"), 40):
        problems.append("source_commit is not a full commit")
    return problems


def _check_problems(record: dict[str, Any]) -> list[str]:
    checks = record.get("checks")
    if isinstance(checks, list) and not all(isinstance(c, dict) for c in checks):
        return ["a check is not an object"]
    return []


def _definition_problems(record: dict[str, Any]) -> list[str]:
    ending = record.get("ending")
    problems = []
    ready = _text(record.get("definition")) and _hex(record.get("definition_sha256"), 64)
    if ending == READY and not ready:
        problems.append("a ready definition holds no file and digest")
    if ending == UNUSABLE and (record.get("definition") or record.get("definition_sha256")):
        problems.append("an unusable definition holds a file or digest")
    return problems


def _schema(record: dict[str, Any]) -> list[str]:
    """What is wrong with the shape of a recorded ``define`` object (empty: it is well formed)."""
    return (
        _name_problems(record)
        + _count_problems(record)
        + _check_problems(record)
        + _definition_problems(record)
    )


def step_defines(steps: Sequence[tuple[str, Path]]) -> tuple[list[StepDefine], list[str]] | None:
    """(defining steps' records, steps whose records cannot be read); ``None`` when the
    records show no define stage at all (the E family does not apply)."""
    found: list[StepDefine] = []
    unread: list[str] = []
    named = False
    for step, agent in steps:
        workflow = _json(agent / WORKFLOW_RECORD)
        if not isinstance(workflow, dict):
            unread.append(step)
            continue
        if "define" not in (workflow.get("stages") or []):
            continue
        named = True
        record = workflow.get("define")
        if not isinstance(record, dict):
            unread.append(step)
            continue
        verify = workflow.get("verify")
        ending = verify.get("ending") if isinstance(verify, dict) else None
        found.append(StepDefine(step, agent, record, ending if isinstance(ending, str) else None))
    if not found and not named:
        return None
    return found, unread


def _draft(define: StepDefine) -> tuple[Draft | None, str | None]:
    """(the recorded draft, why it cannot be read)."""
    raw = _bytes(define.agent, define.record, "state")
    if raw is None:
        return None, f"{define.step}: the define state is missing"
    try:
        return Draft.read(json.loads(raw)), None
    except (ValueError, DraftError) as error:
        return None, f"{define.step}: the define state does not read ({error})"


def _context(define: StepDefine) -> tuple[tool.Context | None, str | None]:
    raw = _bytes(define.agent, define.record, "inputs")
    if raw is None:
        return None, f"{define.step}: the define inputs are missing"
    try:
        record = json.loads(raw)
        if not isinstance(record, dict):
            raise ValueError("not an object")
        return tool.Context.read(record), None
    except ValueError as error:
        return None, f"{define.step}: the define inputs do not read ({error})"


def _e1_step(define: StepDefine) -> tuple[list[str], str | None]:
    if define.verify_ending != GATE_PASSED:
        return [], None
    step = define.step
    ending = define.record.get("ending")
    if ending != READY:
        return [f"{step}: the gate passed but define ended {ending!r}"], None
    draft, why = _draft(define)
    context, context_why = _context(define)
    if draft is None or context is None:
        return [], why or context_why
    written = _bytes(define.agent, define.record, "definition")
    if written is None:
        return [], f"{step}: the recorded definition file is missing"
    return _ready_definition(define, draft, context, written), None


def _ready_definition(
    define: StepDefine, draft: Draft, context: tool.Context, written: bytes
) -> list[str]:
    step = define.step
    if draft.sealed != "ready":
        return [f"{step}: the recorded draft is sealed {draft.sealed!r}, not 'ready'"]
    try:
        faults = violations(draft.reply("ready"), INTAKE_REPLY_SCHEMA)
    except (ValueError, KeyError, TypeError) as error:
        faults = [f"the reply cannot be built ({error})"]
    if faults:
        return [f"{step}: the draft breaks the intake reply schema: {faults[0]}"]
    found = [
        f"{step}: the tool's check fails on the draft: {item.text}"
        for item in tool.recheck(draft, context, "ready").findings
        if not item.advisory
    ]
    if _sha(written) != define.record.get("definition_sha256"):
        found.append(f"{step}: the definition file is not the recorded digest")
    if tool.definition_text(draft, context).encode("utf-8") != written:
        found.append(f"{step}: the definition file is not the draft's render")
    return found


def _events(define: StepDefine) -> list[dict[str, Any]] | None:
    raw = _bytes(define.agent, define.record, "events")
    if raw is None:
        return None
    rows = []
    for line in filter(str.strip, raw.decode("utf-8", errors="replace").splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        rows.append(row if isinstance(row, dict) else {})
    return rows


def _call_event_mismatches(
    step: str, calls_raw: bytes, events: list[dict[str, Any]], calls: list[tool.Call]
) -> list[str]:
    lines = [
        line for line in calls_raw.decode("utf-8", errors="replace").splitlines() if line.strip()
    ]
    if len(calls) != len(lines) or len(events) != len(calls):
        return [f"{step}: {len(lines)} call line(s), {len(calls)} readable, {len(events)} event(s)"]
    return [
        f"{step}: call {n + 1} is {call.command!r}, its event {event.get('command')!r}"
        for n, (call, event) in enumerate(zip(calls, events, strict=True))
        if event.get("command") != call.command
    ]


def _context_drift(step: str, events: list[dict[str, Any]], inputs: bytes) -> list[str]:
    contexts = [e for e in events if e.get("context_sha256") is not None]
    if contexts and contexts[-1].get("context_sha256") != _sha(inputs):
        return [f"{step}: the last call ran under a context other than the recorded inputs"]
    return []


def _e2_step(define: StepDefine) -> tuple[list[str], str | None]:
    step = define.step
    draft, why = _draft(define)
    calls_raw = _bytes(define.agent, define.record, "calls")
    events = _events(define)
    if draft is None:
        return [], why
    if calls_raw is None or events is None:
        return [], f"{step}: the define calls or events are missing"
    calls = tool.parse_calls(calls_raw.decode("utf-8", errors="replace"))
    found = _call_event_mismatches(step, calls_raw, events, calls)
    inputs = _bytes(define.agent, define.record, "inputs")
    if inputs is None:
        return found, f"{step}: the define inputs are missing"
    found += _context_drift(step, events, inputs)
    found += _state_consistency(define, draft, events, calls)
    return found, None


def _state_hash_problem(
    step: str, draft: Draft, events: list[dict[str, Any]], state: bytes
) -> str | None:
    held = [e for e in events if e.get("state_sha256") is not None]
    if not held:
        if draft.items or draft.sealed:
            return f"{step}: the state holds a draft no call recorded"
        return None
    if held[-1].get("state_sha256") != _sha(state):
        return f"{step}: the state is not the one the last call left"
    if held[-1].get("sealed") not in (None, draft.sealed):
        return f"{step}: the state's seal {draft.sealed!r} is not the last call's"
    return None


def _state_consistency(
    define: StepDefine, draft: Draft, events: list[dict[str, Any]], calls: list[tool.Call]
) -> list[str]:
    step = define.step
    found: list[str] = []
    state = _bytes(define.agent, define.record, "state") or b""
    problem = _state_hash_problem(step, draft, events, state)
    if problem:
        found.append(problem)
    if draft.sealed:
        done = [call for call in calls if call.command == "done"]
        if not done or done[-1].blocking:
            found.append(f"{step}: the state is sealed but no `done` call sealed it")
    return found


def _unusable_kept(record: dict[str, Any]) -> bool:
    return record.get("definition") is not None or bool(record.get("definition_sha256"))


def _last_check(record: dict[str, Any]) -> Any:
    checks = record.get("checks")
    return checks[-1] if isinstance(checks, list) and checks else None


def _e3_step(define: StepDefine) -> tuple[list[str], str | None]:
    if define.record.get("ending") != UNUSABLE:
        return [], None
    step = define.step
    found = []
    if _unusable_kept(define.record):
        found.append(f"{step}: an unusable definition was kept")
    last = _last_check(define.record)
    if not isinstance(last, dict):
        return found, f"{step}: no define check is recorded for the unusable ending"
    draft, why = _draft(define)
    if draft is None:
        return found, why
    if draft.sealed == "ready" and not last.get("blocking"):
        found.append(f"{step}: unusable, but its draft is sealed ready and its last check passed")
    if define.verify_ending == GATE_PASSED:
        found.append(f"{step}: the gate passed on an unusable definition")
    return found, None


def _family(
    check_id: str,
    defines: list[StepDefine],
    unread: list[str],
    per_step: Callable[[StepDefine], tuple[list[str], str | None]],
    passed: Callable[[list[StepDefine]], str],
) -> HealthCheck:
    findings: list[str] = []
    unmeasured = [f"{step}: no readable define record" for step in unread]
    for define in defines:
        problems = _schema(define.record)
        if problems:
            unmeasured.append(f"{define.step}: the define record is malformed ({problems[0]})")
            continue
        found, missing = per_step(define)
        findings += found
        unmeasured += [missing] if missing else []
    if findings:
        more = f"; and {len(findings) - _SHOWN} more" if len(findings) > _SHOWN else ""
        detail = "; ".join(findings[:_SHOWN]) + more
        return make_check(check_id, VIOLATION, detail, findings=findings, unmeasured=unmeasured)
    if unmeasured:
        return make_check(check_id, UNMEASURED, "; ".join(unmeasured), unmeasured=unmeasured)
    return make_check(check_id, PASS, passed(defines), steps=[define.step for define in defines])


def _count(defines: list[StepDefine], ending: str) -> int:
    return sum(1 for d in defines if d.record.get("ending") == ending)


def definition_family(steps: Sequence[tuple[str, Path]]) -> tuple[HealthCheck, ...]:
    """``E1``-``E3`` over the steps' agent logs; none when no step ran a define stage."""
    read = step_defines(steps)
    if read is None:
        return ()
    defines, unread = read
    return (
        _family(
            "E1",
            defines,
            unread,
            _e1_step,
            lambda ds: f"{sum(d.verify_ending == GATE_PASSED for d in ds)} gate-passed "
            "checkpoint(s) hold a sealed, passing definition",
        ),
        _family(
            "E2",
            defines,
            unread,
            _e2_step,
            lambda ds: f"{len(ds)} step(s): recorded calls agree with the final draft",
        ),
        _family(
            "E3",
            defines,
            unread,
            _e3_step,
            lambda ds: f"{_count(ds, UNUSABLE)} unusable definition(s) recorded, none passed",
        ),
    )


def check_definition(a: Audit) -> tuple[HealthCheck, ...]:
    return definition_family(
        [(s.step, a.trial_dir / "steps" / s.step / "agent") for s in a.attempted]
    )
