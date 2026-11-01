"""Run-health family ``W``: an enabled stage's lab-owned evidence must exist (design section 6).

``unmeasured`` is a reading, not a verdict: the G, E, J and P8 families call missing evidence
``unmeasured`` and the trial stays valid. That let a trial whose gate never ran, or whose records
the agent deleted, count as a valid agent outcome. This family closes the hole with one explicit
table, ``ROWS``: for each stage the trial's own configuration enabled, one row per kind of
evidence the lab owns. A row's check is a ``violation`` when a REQUIRED artifact is missing (the
file, not just the field that names it) or says the lab's instrument failed. The content checks
stay with G, E, J and P8; the rows reuse their readers (``isolation._source_findings``,
``judge._call_evidence`` ...) so a missing artifact is named the same way in both.

What is *expected* comes from the trial's lab-written Harbor configuration (``config.json``, else
``result.json``'s ``config``: the agent's resolved workflow definition), never from
``workflow.json``, which the agent's logs mount can reach. The arm override on the trial record
(``oracle``/``nop``) is the other authoritative source. A trial that ran a real agent but whose
configuration cannot be read has unknown applicability: every row is then a violation (the
workflow instrument cannot say what it owed), never a fallback to what the records name.

Distinct on purpose:

- an *inapplicable* row: the stage is not enabled, or the arm is a no-native override
  (``oracle``/``nop``), which runs no workflow at all: ``pass`` with the reason;
- *agent outcomes* that leave their evidence in place stay valid: ``gate_refused``,
  ``definition_unusable``, a ``deadline``/``turn_error`` cut. Terminal outcomes excuse exactly the
  artifacts they cannot have (no seal after a cut; no state, criteria or judge pass for an
  unusable definition) and nothing else.

======  =========  ==========================================================================
Row     Stage      Required artifacts; a violation when one is missing
======  =========  ==========================================================================
W1      workflow   ``workflow.json`` readable, with turns, every enabled stage named and its
                   record present; unknown applicability (no readable configuration)
W2      workflow   per turn: a ``session_audit`` with records and sources, and each audited
                   native source file on disk (a deleted rollout is missing evidence)
W3      verify     the gate configuration file at its recorded digest; a gate record per seal
                   and its ``gate-<n>.json`` sidecar (readable, same verdict), unless the
                   phase was cut
W4      verify     the gate ran: ending is not ``gate_error`` (an instrument failure)
W5      verify     the hook run log, readable, at its recorded digest; a reading: a gap is
                   ``unmeasured``, never a violation (the agent's user writes that log)
W6      verify     the ``setup_check`` record of ``prepare``
W7      verify     the setup check passed (a failed one is an instrument failure)
W8      define     the define record and its inputs file; for a ``definition_ready`` ending
                   also the state, call log, event log and final definition at its digest
W9      define     an ``definition_unusable`` ending carries the lab's closed-state record
W10     judge      the baseline record file; unless cut or ``definition_unusable``: a final
                   judge pass and every raw request, response and result file of every call
W11     workflow   any ``integrity`` finding the lab recorded (a session log or watched state
                   rewritten, touched or removed), on the workflow, verify or a turn; the hook
                   log's own findings (``hook_log_*``) are readings, listed under ``G1``
======  =========  ==========================================================================
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError
from raidar_contract.workflow import PACKAGE_SCHEMA, PackageWorkflowDefinition

from raidar.contract.harbor_result import RESULT_FILE
from raidar.engine.health.base import Audit, make_check
from raidar.engine.health.gate import HOOK_LOG_PREFIX, WORKFLOW_RECORD, _hook_runs
from raidar.engine.health.isolation import _source_findings
from raidar.engine.health.judge import JudgeStep, _call_evidence, _closure, _undecided
from raidar.engine.health.model import PASS, UNMEASURED, VIOLATION, HealthCheck

CONFIG_FILE = "config.json"
RECORD_PRIMITIVES = ("frame/1", "install/1", "setup/1", "stop/1", "outputs/1")
"""Primitive records ``PackageCheckpoint.record`` emits for every package workflow."""
GATE_PRIMITIVE = "gate/1"
OVERRIDES = ("oracle", "nop")
CUTS = ("deadline", "turn_error")
"""Verify endings of a cut phase: no seal, no judge pass owed."""
UNUSABLE = "definition_unusable"
GATE_RECORDS = "workflow/verify"
_SHOWN = 3


@dataclass(frozen=True)
class Expected:
    """What the lab's own record of the trial says it must have left behind."""

    kind: str  # "workflow" | "override" | "unknown"
    stages: frozenset[str]
    judge: bool
    packages: tuple[tuple[str, str], ...] | None = None
    """Schema 2 only: each listed package's ``(leaf, release name)`` (``None`` for schema 1)."""
    records: frozenset[str] = frozenset()
    """Schema 2 only: the primitive records the runner owes at the root of ``workflow.json``."""

    @property
    def runs_workflow(self) -> bool:
        return self.kind == "workflow"


UNKNOWN = Expected("unknown", frozenset(), False)
OVERRIDE = Expected("override", frozenset(), False)


@dataclass(frozen=True)
class Step:
    """One attempted step's host-side workflow evidence."""

    name: str
    agent: Path
    workflow: dict[str, Any] | None
    expected: Expected

    def record(self, stage: str) -> dict[str, Any] | None:
        found = (self.workflow or {}).get(stage)
        return found if isinstance(found, dict) else None

    def file(self, relative: Any) -> Path | None:
        """The file a record names, inside the step's agent logs and present; else ``None``."""
        if not isinstance(relative, str) or not relative:
            return None
        name = PurePosixPath(relative)
        if name.is_absolute() or ".." in name.parts:
            return None
        path = self.agent / name.as_posix()
        return path if path.is_file() else None

    def digest(self, relative: Any) -> str | None:
        path = self.file(relative)
        return hashlib.sha256(path.read_bytes()).hexdigest() if path is not None else None


Reader = Callable[[Step], str | None]


@dataclass(frozen=True)
class Row:
    """One evidence row: its check id, the stage that owes it and the reader that finds a gap.

    A ``reading`` row reports a gap as ``unmeasured`` and never as a violation."""

    id: str
    stage: str
    evidence: str
    gap: Reader
    reading: bool = False


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _gap_packages(step: Step) -> str | None:
    """A package workflow's host-written primitive records and each package's install entry."""
    expected = step.expected
    if expected.packages is None:
        return None
    workflow = step.workflow or {}
    if workflow.get("invalid") is not None:
        return "the runner recorded an invalid trial (instrument or integrity failure)"
    return _gap_records(workflow, expected) or _gap_installs(workflow, expected)


def _gap_records(workflow: Mapping[str, Any], expected: Expected) -> str | None:
    absent = sorted(name for name in expected.records if not isinstance(workflow.get(name), dict))
    if absent:
        return f"no record for primitive(s): {', '.join(absent)}"
    if GATE_PRIMITIVE not in expected.records:
        return None
    gate = workflow[GATE_PRIMITIVE]
    if not isinstance(gate.get("gates"), list) or gate.get("errors") != []:
        return "the gate/1 record has no gates list or reports errors (an instrument failure)"
    return None


def _gap_installs(workflow: Mapping[str, Any], expected: Expected) -> str | None:
    installed = workflow["install/1"]
    for leaf, reference in expected.packages or ():
        entry = installed.get(leaf)
        if not isinstance(entry, dict) or entry.get("reference") != reference:
            return f"no install/1 record of {reference} under {leaf!r}"
    return None


def _gap_stage_records(step: Step, workflow: Mapping[str, Any]) -> str | None:
    listed = workflow.get("stages")
    named = {s for s in listed if isinstance(s, str)} if isinstance(listed, list) else set()
    absent = sorted(step.expected.stages - named)
    if absent:
        return f"enabled stage(s) not named in the record: {', '.join(absent)}"
    bare = sorted(s for s in step.expected.stages & {"define", "verify"} if step.record(s) is None)
    return f"no record for enabled stage(s): {', '.join(bare)}" if bare else None


def _gap_workflow(step: Step) -> str | None:
    workflow = step.workflow
    if workflow is None:
        return f"{WORKFLOW_RECORD} is missing or unreadable"
    turns = workflow.get("turns")
    if not isinstance(turns, list) or not turns:
        return "no turn is recorded"
    return _gap_stage_records(step, workflow) or _gap_packages(step)


def _turn_gap(step: Step, number: int, turn: Any) -> str | None:
    audit = turn.get("session_audit") if isinstance(turn, dict) else None
    if not isinstance(audit, dict):
        return f"turn {number}: no session audit"
    if not audit.get("records"):
        return f"turn {number}: the session audit read no records"
    log_dir = turn.get("log_dir")
    relative = PurePosixPath(log_dir) if isinstance(log_dir, str) else None
    if relative is None or relative.is_absolute() or ".." in relative.parts:
        return f"turn {number}: no valid turn log directory"
    sources = audit.get("sources")
    bad, missing = _source_findings(
        sources if isinstance(sources, list) else [], step.agent / log_dir
    )
    problems = [*missing, *bad]
    return f"turn {number}: {problems[0]}" if problems else None


def _gap_session(step: Step) -> str | None:
    turns = (step.workflow or {}).get("turns")
    for number, turn in enumerate(turns if isinstance(turns, list) else [], 1):
        gap = _turn_gap(step, number, turn)
        if gap is not None:
            return gap
    return None


def _seal_gap(step: Step, number: int, gate: Any) -> str | None:
    if not isinstance(gate, dict):
        return f"gate {number}: malformed record"
    sidecar = step.file(gate.get("record"))
    stored = _json(sidecar) if sidecar is not None else None
    if not isinstance(stored, dict):
        return f"gate {number}: sidecar {gate.get('record')!r} is missing or unreadable"
    if stored.get("verdict") != gate.get("verdict"):
        return f"gate {number}: sidecar verdict {stored.get('verdict')!r} differs from the record"
    return None


def _gap_gates(step: Step) -> str | None:
    verify = step.record("verify") or {}
    config = verify.get("gate_config_sha256")
    if config is None or step.digest(f"{GATE_RECORDS}/gate.json") != config:
        return "the gate configuration file is missing or not at its recorded digest"
    gates = verify.get("gates")
    if not isinstance(gates, list) or not gates:
        return None if verify.get("ending") in CUTS else "no gate record"
    for number, gate in enumerate(gates, 1):
        gap = _seal_gap(step, number, gate)
        if gap is not None:
            return gap
    return None


def _gap_gate_error(step: Step) -> str | None:
    ending = (step.record("verify") or {}).get("ending")
    return "the gate could not run (gate_error)" if ending == "gate_error" else None


def _gap_hook(step: Step) -> str | None:
    verify = step.record("verify") or {}
    if _hook_runs(step.agent, verify) is None:
        return "the hook's run log is missing or unreadable"
    if verify.get("hook_log_sha256") != step.digest(verify.get("hook_log")):
        return "the hook's run log is not at its recorded digest"
    return None


def _setup(step: Step) -> dict[str, Any] | None:
    setup = (step.record("verify") or {}).get("setup_check")
    return setup if isinstance(setup, dict) and isinstance(setup.get("status"), str) else None


def _gap_setup_recorded(step: Step) -> str | None:
    return None if _setup(step) is not None else "no setup check record"


def _gap_setup_passed(step: Step) -> str | None:
    setup = _setup(step)
    if setup is None or setup["status"] == "passed":
        return None
    return f"setup check {setup['status']}"


def _gap_define(step: Step) -> str | None:
    record = step.record("define")
    if record is None:
        return "no define record"
    ending = record.get("ending")
    if not isinstance(ending, str):
        return "the define record has no ending"
    if not isinstance(record.get("checks"), list):
        return "the define record has no checks"
    if step.file(record.get("inputs")) is None:
        return "the define inputs file is missing"
    if ending != "definition_ready":
        return None
    return _gap_ready_definition(step, record)


def _gap_ready_definition(step: Step, record: dict[str, Any]) -> str | None:
    required = ("state", "calls", "events", "definition")
    absent = [key for key in required if step.file(record.get(key)) is None]
    if absent:
        return f"a ready definition is missing its {', '.join(absent)} file(s)"
    if step.digest(record.get("definition")) != record.get("definition_sha256"):
        return "the definition file is not at its recorded digest"
    return None


def _gap_closed(step: Step) -> str | None:
    record = step.record("define") or {}
    if record.get("ending") == UNUSABLE and record.get("closed") is not True:
        return "an unusable definition without the lab's closed-state record"
    return None


def _judge_excused(step: Step) -> bool:
    """A cut phase or an unusable definition owes no judge pass (no tree sealed, no criteria)."""
    verify = step.record("verify") or {}
    return verify.get("ending") in CUTS or (step.record("define") or {}).get("ending") == UNUSABLE


def _integrity_names(source: Mapping[str, Any]) -> list[str]:
    entries = source.get("integrity")
    names: list[str] = []
    for entry in entries if isinstance(entries, list) else []:
        name = entry.get("name") if isinstance(entry, dict) else None
        names.append(str(name or entry))
    return names


def _integrity_sources(step: Step) -> list[Mapping[str, Any]]:
    """The workflow record, the verify record and every turn record that may carry findings."""
    workflow = step.workflow or {}
    turns = workflow.get("turns")
    sources: list[Mapping[str, Any]] = [workflow, step.record("verify") or {}]
    sources += [t for t in turns if isinstance(t, dict)] if isinstance(turns, list) else []
    return sources


def _validity_findings(source: Mapping[str, Any]) -> list[str]:
    """Findings that invalidate: everything except readings of the agent-writable hook log."""
    return [n for n in _integrity_names(source) if not n.startswith(HOOK_LOG_PREFIX)]


def _gap_integrity(step: Step) -> str | None:
    """Any integrity finding the lab recorded (a rewritten session log or watched state) is a
    violation; findings about the agent-writable hook log are readings and do not count."""
    found = [n for source in _integrity_sources(step) for n in _validity_findings(source)]
    return f"integrity finding(s): {', '.join(dict.fromkeys(found))}" if found else None


def _gap_judge(step: Step) -> str | None:
    verify = step.record("verify") or {}
    if not isinstance(verify.get("judge"), dict):
        return "no judge settings in the verify record"
    if not isinstance(verify.get("judge_calls"), list) or not isinstance(
        verify.get("judged"), list
    ):
        return "no judge_calls or judged list in the verify record"
    baseline = verify.get("baseline")
    if not isinstance(baseline, dict) or step.file(baseline.get("record")) is None:
        return "the baseline record file is missing"
    if _judge_excused(step):
        return None
    return _gap_judge_pass(step, verify)


def _gap_judge_pass(step: Step, verify: dict[str, Any]) -> str | None:
    if not verify["judged"]:
        return "no judge pass is recorded"
    judging = JudgeStep(step.name, step.agent, verify)
    for read in (_call_evidence, _closure, _undecided):
        _, missing = read(judging)
        if missing:
            return missing[0]
    return None


ROWS: tuple[Row, ...] = (
    Row("W1", "workflow", "workflow record and each enabled stage's record", _gap_workflow),
    Row("W2", "workflow", "session audit of every turn and its native source files", _gap_session),
    Row("W3", "verify", "gate configuration and per-seal records", _gap_gates),
    Row("W4", "verify", "the gate ran", _gap_gate_error),
    Row("W5", "verify", "hook run log", _gap_hook, reading=True),
    Row("W6", "verify", "deterministic setup check record", _gap_setup_recorded),
    Row("W7", "verify", "the setup check passed", _gap_setup_passed),
    Row("W8", "define", "define records", _gap_define),
    Row("W9", "define", "closed-state record of an unusable definition", _gap_closed),
    Row("W10", "judge", "judge records", _gap_judge),
    Row("W11", "workflow", "no integrity finding on the hook or session logs", _gap_integrity),
)


def _agent_config(trial_dir: Path) -> Mapping[str, Any] | None:
    result = _json(trial_dir / RESULT_FILE)
    for source in (_json(trial_dir / CONFIG_FILE), result.get("config") if result else None):
        agent = source.get("agent") if isinstance(source, dict) else None
        if isinstance(agent, dict):
            return agent
    return None


def _stage_enabled(stages: Mapping[str, Any], name: str) -> bool:
    settings = stages.get(name)
    return isinstance(settings, dict) and settings.get("enabled") is True


def _definition(agent: Mapping[str, Any]) -> Any:
    kwargs = agent.get("kwargs")
    return kwargs.get("definition") if isinstance(kwargs, dict) else None


def _schema_one(definition: Any) -> Expected | None:
    stages = definition.get("stages") if isinstance(definition, dict) else None
    if not isinstance(stages, dict):
        return None
    enabled = {"build"} | {s for s in ("define", "verify") if _stage_enabled(stages, s)}
    verify = stages.get("verify") if "verify" in enabled else None
    judge = isinstance(verify, dict) and verify.get("judge") is not None
    return Expected("workflow", frozenset(enabled), judge)


def _kwargs(agent: Mapping[str, Any]) -> Mapping[str, Any]:
    kwargs = agent.get("kwargs")
    return kwargs if isinstance(kwargs, dict) else {}


def _schema_two(definition: Any, kwargs: Mapping[str, Any]) -> Expected:
    """A ``schema: 2`` package workflow, read from the lab's own definition and runner kwargs.

    The runner's ``PackageCheckpoint.record`` always emits ``RECORD_PRIMITIVES`` at the root of
    ``workflow.json`` and adds ``gate/1`` when the arm's ``primitives`` kwarg (the arm's
    ``primitive_code``: contract id to code digest) lists it. Each listed package must also be
    locked (``locked_packages``, keyed by release name) and leave an ``install/1`` entry under its
    leaf name that names that release. A definition the contract rejects, a package workflow
    whose kwargs omit its locked packages or runner identities, or one asking for a reply or judge
    (whose evidence this table cannot yet read) has unknown applicability."""
    try:
        parsed = PackageWorkflowDefinition.model_validate(definition)
    except ValidationError:
        return UNKNOWN
    if parsed.judge is not None or parsed.build.reply is not None:
        return UNKNOWN
    uses = tuple((use.release.leaf, use.release.name) for use in parsed.stages)
    primitives = kwargs.get("primitives")
    if uses and not _runner_kwargs(primitives, kwargs.get("locked_packages"), uses):
        return UNKNOWN
    records = set(RECORD_PRIMITIVES)
    if isinstance(primitives, dict) and GATE_PRIMITIVE in primitives:
        records.add(GATE_PRIMITIVE)
    return Expected("workflow", frozenset({"build"}), False, uses, frozenset(records))


def _runner_kwargs(primitives: Any, locked: Any, uses: tuple[tuple[str, str], ...]) -> bool:
    """A package arm's kwargs name the runner's primitives and lock every listed package."""
    if not isinstance(primitives, dict) or not isinstance(locked, dict):
        return False
    return {"frame/1", "install/1", "runner/1"} <= set(primitives) and all(
        reference in locked for _, reference in uses
    )


def configured(trial_dir: Path, override: str | None) -> Expected:
    """What the lab's own record of the trial enables; ``UNKNOWN`` when it cannot be read."""
    if override:
        return OVERRIDE
    agent = _agent_config(trial_dir)
    if agent is None:
        return UNKNOWN
    definition = _definition(agent)
    if isinstance(definition, dict) and definition.get("schema") == PACKAGE_SCHEMA:
        return _schema_two(definition, _kwargs(agent))
    found = _schema_one(definition)
    if found is not None:
        return found
    return OVERRIDE if agent.get("name") in OVERRIDES else UNKNOWN


def _applies(row: Row, expected: Expected) -> bool:
    if not expected.runs_workflow:
        return False
    if row.stage == "workflow":
        return True
    if row.stage == "judge":
        return expected.judge
    return row.stage in expected.stages


def _step(audit: Audit, name: str, expected: Expected) -> Step:
    agent = audit.trial_dir / "steps" / name / "agent"
    raw = _json(agent / WORKFLOW_RECORD)
    return Step(name, agent, raw if isinstance(raw, dict) else None, expected)


def _gap_report(row: Row, ruled: Sequence[Step]) -> str | None:
    gaps = [f"{s.name}: {why}" for s in ruled if (why := row.gap(s)) is not None]
    if not gaps:
        return None
    shown = "; ".join(gaps[:_SHOWN]) + (" ..." if len(gaps) > _SHOWN else "")
    kind = "unread" if row.reading else "missing lab"
    return f"{kind} evidence ({row.evidence}): {shown}"


def _check(row: Row, steps: Sequence[Step], expected: Expected) -> HealthCheck:
    gap_status = UNMEASURED if row.reading else VIOLATION
    if expected.kind == "unknown":
        return make_check(
            row.id,
            gap_status,
            "applicability unknown: no readable trial configuration for a real agent run",
        )
    ruled = [s for s in steps if _applies(row, s.expected)]
    if not ruled:
        return make_check(row.id, PASS, f"not applicable: {row.stage} owes no {row.evidence}")
    report = _gap_report(row, ruled)
    if report is not None:
        return make_check(row.id, gap_status, report)
    return make_check(row.id, PASS, f"{row.evidence}: present", steps=[s.name for s in ruled])


def evidence_family(audit: Audit) -> tuple[HealthCheck, ...]:
    """One check per table row over the attempted steps; none when no step ran."""
    if not audit.attempted:
        return ()
    expected = configured(audit.trial_dir, audit.record.agent_override)
    steps = [_step(audit, s.step, expected) for s in audit.attempted]
    return tuple(_check(row, steps, expected) for row in ROWS)
