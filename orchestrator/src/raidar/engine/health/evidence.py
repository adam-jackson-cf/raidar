"""Run-health family ``W``: the lab-owned evidence a locked primitive owes (design section 6).

``unmeasured`` is a reading, not a verdict: the G, E, J and P8 families call missing evidence
``unmeasured`` and the trial stays valid. That let a trial whose gate never ran, or whose records
the agent deleted, count as a valid agent outcome. This family closes the hole with one explicit
table, ``ROWS``: for each primitive the trial's arm locked, one row per kind of evidence the lab
owns. A row's check is a ``violation`` when a REQUIRED artifact is missing (the file, not just the
field that names it), is malformed or inconsistent, or says the lab's instrument failed. The content
checks stay with G, E, J and P8; the rows reuse their readers (``isolation._source_findings``,
``judge._call_evidence`` ...) so a missing artifact is named the same way in both.

What is *expected* comes from the trial's lab-written Harbor configuration (``config.json``, else
``result.json``'s ``config``: the agent's resolved ``schema: 2`` definition and the runner kwargs
``locked_packages`` and ``primitives``, the arm's locked primitive code), never from
``workflow.json``, which the agent's logs mount can reach. A primitive is owed exactly when the lock
lists its contract id; no stage name is inferred. The arm override on the trial record
(``oracle``/``nop``) is the other authoritative source. A trial that ran a real agent but whose
configuration cannot be read, whose definition the contract rejects, whose locked primitives name
an unknown contract version, or whose definition and locked primitives disagree (a judge or reply
declared without its primitive, a primitive without its declaration) has unknown applicability:
every row is then a violation (the workflow instrument cannot say what it owed), never a fallback
to what the records name.

Distinct on purpose:

- an *inapplicable* row: the primitive is not locked, or the arm is a no-native override
  (``oracle``/``nop``), which runs no workflow at all: ``pass`` with the reason;
- *agent outcomes* that leave their evidence in place stay valid: a refused gate, a closed stop, an
  agent-attributed gate error, a ``deadline`` cut. Terminal outcomes excuse exactly the artifacts
  they cannot have (no seal after a cut; no judge pass without criteria) and nothing else.

The generic families over declared triggers, calls and outputs are phase 6; these rows read the
records ``PackageCheckpoint.record`` and the judge and reply wiring write at the root of
``workflow.json``, keyed by primitive contract id.

======  ==============================  =======================================================
Row     Primitive                       Required artifacts; a violation when one is missing
======  ==============================  =======================================================
W1      (workflow)                      ``workflow.json`` readable, with turns, the build named
                                        and every primitive record owed; the package runner
                                        recorded no invalid trial; each locked package's install
W2      (workflow)                      per turn: a ``session_audit`` with records and sources,
                                        and each audited native source file on disk
W3      ``gate/1``                      a gate record per seal and its ``gate-<n>.json``
                                        sidecar (readable, same verdict, attributed), unless cut
W4      ``gate/1``                      the gate ran: the canary could decide (a refused canary
                                        is healthy), no instrument or integrity attribution,
                                        no gate errors
W5      ``gate/1``                      the hook run log, readable; a reading: a gap is
                                        ``unmeasured``, never a violation (the agent's user
                                        writes that log)
W6      ``gate/1``                      the ``setup_check`` record of ``prepare``
W7      ``gate/1``                      the setup check passed (a failed one is an instrument
                                        failure)
W8      ``setup/1`` ``stop/1``          for each package whose locked manifest declares it (the
        ``outputs/1``                   lab's ``declared`` record): a call record of every setup
                                        and stop call under its qualified release name, and a
                                        copy record of every declared output, at its digest
W9      ``stop/1``                      the stop state is consistent with its decisions: rounds,
                                        repair and closed agree; a closed package is never
                                        reopened; every unusable classification carries a canary
W10     ``jev-judge/1``                 the baseline record file; unless cut or without
        ``acceptance-reply/1``          criteria: criteria at their output's digest, a final
                                        judge pass and every raw request, response and result
                                        file of every call; every reply record at its digest
W11     (workflow)                      any ``integrity`` finding the lab recorded (a session log
                                        or watched state rewritten, touched or removed), on the
                                        workflow, the gate or a turn; the hook log's own findings
                                        (``hook_log_*``) are readings, never validity
======  ==============================  =======================================================
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

from raidar.contract.file_io import load_bytes, load_text
from raidar.contract.harbor_result import RESULT_FILE
from raidar.engine.health.base import make_check
from raidar.engine.health.isolation import _agent_reader, _source_findings
from raidar.engine.health.judge import JudgeStep, _call_evidence, _closure, _undecided
from raidar.engine.health.model import PASS, UNMEASURED, VIOLATION, HealthCheck

CONFIG_FILE = "config.json"
WORKFLOW_RECORD = "workflow/workflow.json"
HOOK_LOG_PREFIX = "hook_log"
RECORD_PRIMITIVES = ("frame/1", "install/1", "setup/1", "stop/1", "outputs/1")
"""Primitive records ``PackageCheckpoint.record`` emits for every package workflow."""
GATE_PRIMITIVE = "gate/1"
SETUP_PRIMITIVE = "setup/1"
STOP_PRIMITIVE = "stop/1"
OUTPUTS_PRIMITIVE = "outputs/1"
JUDGE_PRIMITIVE = "jev-judge/1"
REPLY_PRIMITIVE = "acceptance-reply/1"
RUNNER_PRIMITIVE = "runner/1"
KNOWN_PRIMITIVES = frozenset(
    {
        "frame/1",
        "install/1",
        RUNNER_PRIMITIVE,
        SETUP_PRIMITIVE,
        GATE_PRIMITIVE,
        STOP_PRIMITIVE,
        OUTPUTS_PRIMITIVE,
        JUDGE_PRIMITIVE,
        REPLY_PRIMITIVE,
    }
)
"""Every primitive contract id (with version) the health table can read; a locked id outside this
set is an unknown version and leaves applicability unknown."""
OVERRIDES = ("oracle", "nop")
CUT = "deadline"
"""An ``endings`` value of a cut phase: no seal and no judge pass owed."""
NO_CRITERIA = "no_criteria"
GATE_DIR = "gate"
HOOK_LOG = f"{GATE_DIR}/hook-runs.jsonl"
CALL_SCHEMA = "call/1"
NO_STOP_CALL = "no stop call"
STOP_ACTIONS = ("done", "resume", "repair", "closed")
UNREADABLE_ATTRIBUTIONS = ("instrument_invalid", "integrity_invalid")
DECIDED = ("passed", "refused")
"""Verdicts of a gate or canary that could decide; ``error`` is an instrument failure."""
_SHOWN = 3


@dataclass(frozen=True)
class Declared:
    """What one locked package's manifest declares: the lab's authoritative account of which
    setup, stop and output records the package owes (recorded in the trial configuration)."""

    leaf: str
    release: str
    """The qualified release name (``raidar/define``) the host's call records carry."""
    setup: bool
    stop: bool
    outputs: tuple[str, ...]


@dataclass(frozen=True)
class Expected:
    """What the lab's own record of the trial says it must have left behind."""

    kind: str  # "workflow" | "override" | "unknown"
    locked: frozenset[str] = frozenset()
    """The primitive contract ids the lock lists (the arm's ``primitive_code``)."""
    packages: tuple[tuple[str, str], ...] = ()
    """Each listed package's ``(leaf, release name)``."""
    records: frozenset[str] = frozenset()
    """The primitive records the runner owes at the root of ``workflow.json``."""
    declared: tuple[Declared, ...] = ()
    """Each listed package's manifest declarations, in workflow order."""
    why: str = ""
    """For ``unknown`` kind: why applicability cannot be read."""

    @property
    def runs_workflow(self) -> bool:
        return self.kind == "workflow"


def _unknown(why: str) -> Expected:
    return Expected("unknown", why=why)


UNKNOWN = _unknown("no readable trial configuration for a real agent run")
OVERRIDE = Expected("override")


@dataclass(frozen=True)
class Step:
    """One attempted step's host-side workflow evidence."""

    name: str
    agent: Path
    workflow: dict[str, Any] | None
    expected: Expected

    def record(self, primitive: str) -> dict[str, Any] | None:
        """The record of one primitive contract id at the root of ``workflow.json``."""
        found = (self.workflow or {}).get(primitive)
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
        return hashlib.sha256(load_bytes(path)).hexdigest() if path is not None else None

    @property
    def cut(self) -> bool:
        """The runner ended a package's checkpoint at the deadline (no seal, no judge pass)."""
        endings = (self.workflow or {}).get("endings")
        return isinstance(endings, dict) and CUT in endings.values()


Reader = Callable[[Step], str | None]


@dataclass(frozen=True)
class Row:
    """One evidence row: its check id, the primitives that owe it and the reader that finds a gap.

    A row applies when any of its ``primitives`` is locked; its reader reads only the locked ones.
    A row with no ``primitives`` is owed by every workflow. A ``reading`` row reports a gap as
    ``unmeasured`` and never as a violation."""

    id: str
    primitives: tuple[str, ...]
    evidence: str
    gap: Reader
    reading: bool = False


def _json(path: Path) -> Any:
    try:
        return json.loads(load_text(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _locked(step: Step, primitive: str) -> bool:
    return primitive in step.expected.locked


def _gap_packages(step: Step) -> str | None:
    """A package workflow's host-written primitive records and each package's install entry."""
    workflow = step.workflow or {}
    if workflow.get("invalid") is not None:
        return "the runner recorded an invalid trial (instrument or integrity failure)"
    return _gap_records(workflow, step.expected) or _gap_installs(workflow, step.expected)


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
    for leaf, reference in expected.packages:
        entry = installed.get(leaf)
        if not isinstance(entry, dict) or entry.get("reference") != reference:
            return f"no install/1 record of {reference} under {leaf!r}"
    return None


def _gap_stage_records(workflow: Mapping[str, Any]) -> str | None:
    listed = workflow.get("stages")
    named = {s for s in listed if isinstance(s, str)} if isinstance(listed, list) else set()
    return None if "build" in named else "the build stage is not named in the record"


def _gap_workflow(step: Step) -> str | None:
    workflow = step.workflow
    if workflow is None:
        return f"{WORKFLOW_RECORD} is missing or unreadable"
    turns = workflow.get("turns")
    if not isinstance(turns, list) or not turns:
        return "no turn is recorded"
    return _gap_stage_records(workflow) or _gap_packages(step)


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
        sources if isinstance(sources, list) else [], _agent_reader(step.agent), relative
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


# gate/1 ------------------------------------------------------------------------------------


def _gate(step: Step) -> dict[str, Any]:
    return step.record(GATE_PRIMITIVE) or {}


def _seal_gap(step: Step, number: int, gate: Any) -> str | None:
    if not isinstance(gate, dict):
        return f"gate {number}: malformed record"
    if not isinstance(gate.get("attribution"), str):
        return f"gate {number}: no attribution"
    sidecar = step.file(gate.get("record"))
    stored = _json(sidecar) if sidecar is not None else None
    if not isinstance(stored, dict):
        return f"gate {number}: sidecar {gate.get('record')!r} is missing or unreadable"
    if stored.get("verdict") != gate.get("verdict"):
        return f"gate {number}: sidecar verdict {stored.get('verdict')!r} differs from the record"
    return None


def _gap_gates(step: Step) -> str | None:
    gates = _gate(step).get("gates")
    if not isinstance(gates, list):
        return "the gate/1 record has no gates list"
    if not gates:
        return None if step.cut else "no gate record"
    for number, gate in enumerate(gates, 1):
        gap = _seal_gap(step, number, gate)
        if gap is not None:
            return gap
    return None


def _gap_gate_ran(step: Step) -> str | None:
    gate = _gate(step)
    canary = gate.get("canary")
    if not isinstance(canary, dict) or canary.get("verdict") not in DECIDED or canary.get("error"):
        return "the gate canary is missing or could not decide (an instrument failure)"
    if gate.get("errors") != []:
        return "the gate reported errors (an instrument failure)"
    for number, seal in enumerate(gate.get("gates") or [], 1):
        attribution = seal.get("attribution") if isinstance(seal, dict) else None
        if attribution in UNREADABLE_ATTRIBUTIONS:
            return f"gate {number} ended {attribution} (an instrument or integrity failure)"
    return None


def _gap_hook(step: Step) -> str | None:
    path = step.file(HOOK_LOG)
    if path is None:
        return "the hook's run log is missing"
    try:
        lines = load_text(path, encoding="utf-8").splitlines()
        runs = [json.loads(line) for line in lines if line.strip()]
    except (OSError, ValueError):
        return "the hook's run log is unreadable"
    return None if all(isinstance(run, dict) for run in runs) else "the hook's run log is malformed"


def _setup_check(step: Step) -> dict[str, Any] | None:
    setup = _gate(step).get("setup_check")
    return setup if isinstance(setup, dict) and isinstance(setup.get("status"), str) else None


def _gap_setup_recorded(step: Step) -> str | None:
    return None if _setup_check(step) is not None else "no gate setup check record"


def _gap_setup_passed(step: Step) -> str | None:
    setup = _setup_check(step)
    if setup is None or setup["status"] == "passed":
        return None
    return f"setup check {setup['status']}"


# setup/1, stop/1 and outputs/1 -------------------------------------------------------------


def _call_gap(call: Any, package: str, primitive: str) -> str | None:
    """Why ``call`` is not the host's ``call/1`` record of ``package``'s ``primitive``.

    ``package`` is the qualified release name the call record carries (``raidar/define``), never
    the leaf the primitive records are keyed by."""
    if not isinstance(call, dict) or call.get("schema") != CALL_SCHEMA:
        return f"{package}: no {primitive} call record"
    if call.get("package") != package or call.get("primitive") != primitive:
        return f"{package}: the call record is not of {primitive}"
    if call.get("ran") is not True:
        return f"{package}: the {primitive} call did not run"
    return None


def _owed(
    step: Step, primitive: str, owes: Callable[[Declared], bool]
) -> list[tuple[Declared, dict[str, Any]]] | str:
    """The records of every locked package whose manifest declares ``primitive``, or why one is
    absent or malformed. A package that declares it is owed its record; one the lock does not
    list, or that does not declare it, owes none and leaves none."""
    record = step.record(primitive)
    if record is None:
        return f"no {primitive} record"
    owing = [package for package in step.expected.declared if owes(package)]
    entries = []
    for package in owing:
        entry = record.get(package.leaf)
        if not isinstance(entry, dict):
            return f"{package.release}: no {primitive} record"
        entries.append((package, entry))
    stray = sorted(set(record) - {package.leaf for package in owing})
    return f"{primitive}: a record of undeclared package(s) {stray}" if stray else entries


def _setup_gap(package: Declared, entry: dict[str, Any]) -> str | None:
    invoked = entry.get("call")
    gap = _call_gap(invoked, package.release, SETUP_PRIMITIVE)
    if gap is not None:
        return gap
    assert isinstance(invoked, dict)
    flawed = (
        invoked.get("exit_code") != 0
        or invoked.get("timed_out")
        or invoked.get("overflow")
        or invoked.get("bounds_violation")
        or invoked.get("integrity_error")
    )
    return f"{package.release}: the setup call did not finish cleanly" if flawed else None


def _gap_setup(step: Step) -> str | None:
    entries = _owed(step, SETUP_PRIMITIVE, lambda package: package.setup)
    if isinstance(entries, str):
        return entries
    return next((gap for package, entry in entries if (gap := _setup_gap(package, entry))), None)


def _decision_gap(package: str, number: int, decision: Any) -> str | None:
    where = f"{package}: stop decision {number}"
    if not isinstance(decision, dict) or decision.get("action") not in STOP_ACTIONS:
        return f"{where} is malformed"
    if decision.get("reason") == NO_STOP_CALL:
        return None
    invoked = decision.get("call")
    if not isinstance(invoked, dict):
        return f"{where} has no call record"
    if invoked.get("call") is None:
        bounds = invoked.get("bounds_violation")
        return None if isinstance(bounds, str) and bounds else f"{where} has no call record"
    return _call_gap(invoked["call"], package, STOP_PRIMITIVE)


def _canary_gap(package: Declared, runs: list[Any]) -> str | None:
    """Each canary run's expected output digests name exactly the manifest's declared outputs
    (the stage-check record the runner held the package to)."""
    if not runs:
        return f"{package.release}: no stop canary runs"
    for run in runs:
        expected = run.get("expected") if isinstance(run, dict) else None
        outputs = expected.get("outputs") if isinstance(expected, dict) else None
        if not isinstance(outputs, dict) or set(outputs) != set(package.outputs):
            return f"{package.release}: a stop canary run does not name the declared outputs"
    return None


def _stop_shape(package: Declared, entry: dict[str, Any]) -> str | None:
    reference = package.release
    decisions = entry.get("decisions")
    if not isinstance(decisions, list):
        return f"{reference}: no stop decisions"
    runs = entry.get("canary")
    if not isinstance(runs, list):
        return f"{reference}: no stop canary runs"
    gap = _canary_gap(package, runs)
    if gap is not None:
        return gap
    for number, decision in enumerate(decisions, 1):
        gap = _decision_gap(reference, number, decision)
        if gap is not None:
            return gap
    return None


def _outputs_gap(step: Step, package: Declared, entry: dict[str, Any], done: bool) -> str | None:
    reference = package.release
    stray = sorted(set(entry) - {*package.outputs, "cut"})
    if stray:
        return f"{reference}: copy record of undeclared output(s) {stray}"
    for name in package.outputs:
        output = entry.get(name)
        if not isinstance(output, dict) or "error" not in output or "sha256" not in output:
            return f"{reference}: declared output {name!r} has no copy record"
        if output["error"] is not None:
            if done:
                why = output["error"]
                return f"{reference}: output {name!r} is invalid after a done stop ({why})"
            continue
        digest = step.digest(output.get("host_path"))
        if digest is None or digest != output["sha256"]:
            return f"{reference}: output {name!r} is not copied at its recorded digest"
    return None


def _done_packages(step: Step) -> set[str]:
    """The leaves whose last stop decision reported ``done`` (so every output is owed)."""
    done: set[str] = set()
    for package, entry in (step.record(STOP_PRIMITIVE) or {}).items():
        decisions = entry.get("decisions") if isinstance(entry, dict) else None
        last = decisions[-1] if isinstance(decisions, list) and decisions else None
        if isinstance(last, dict) and last.get("action") == "done":
            done.add(str(package))
    return done


def _gap_outputs(step: Step) -> str | None:
    entries = _owed(step, OUTPUTS_PRIMITIVE, lambda package: bool(package.outputs))
    if isinstance(entries, str):
        return entries
    done = _done_packages(step) if _locked(step, STOP_PRIMITIVE) else set()
    for package, entry in entries:
        gap = _outputs_gap(step, package, entry, package.leaf in done)
        if gap is not None:
            return gap
    return None


def _gap_stop(step: Step) -> str | None:
    entries = _owed(step, STOP_PRIMITIVE, lambda package: package.stop)
    if isinstance(entries, str):
        return entries
    return next((gap for package, entry in entries if (gap := _stop_shape(package, entry))), None)


def _gap_calls(step: Step) -> str | None:
    """A call record for every setup and stop call, and the outputs the done decision owes."""
    readers = (
        (SETUP_PRIMITIVE, _gap_setup),
        (STOP_PRIMITIVE, _gap_stop),
        (OUTPUTS_PRIMITIVE, _gap_outputs),
    )
    for primitive, read in readers:
        if _locked(step, primitive) and (gap := read(step)) is not None:
            return gap
    return None


def _acted(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in decisions if d.get("reason") != NO_STOP_CALL]


def _repair_gap(package: str, entry: dict[str, Any], actions: list[Any]) -> str | None:
    repairs = actions.count("repair")
    if entry.get("repaired") is not (repairs > 0) or repairs > 1:
        return f"{package}: the repair state disagrees with the repair decisions"
    return None


def _closed_gap(package: str, entry: dict[str, Any], actions: list[Any]) -> str | None:
    closed = "closed" in actions
    if entry.get("closed") is not closed:
        return f"{package}: the closed state disagrees with the decisions"
    if closed and actions.index("closed") != len(actions) - 1:
        return f"{package}: a closed stop was reopened"
    return None


def _state_gap(package: str, entry: dict[str, Any]) -> str | None:
    decisions = [d for d in entry.get("decisions") or [] if isinstance(d, dict)]
    actions = [d["action"] for d in _acted(decisions)]
    if entry.get("rounds") != actions.count("resume"):
        return f"{package}: rounds {entry.get('rounds')!r} disagree with the resume decisions"
    return _repair_gap(package, entry, actions) or _closed_gap(package, entry, actions)


def _unusable_gap(package: str, entry: dict[str, Any]) -> str | None:
    for decision in entry.get("decisions") or []:
        attribution = decision.get("attribution") if isinstance(decision, dict) else None
        if (
            attribution is not None
            and attribution != "agent_state"
            and not isinstance(decision.get("canary"), dict)
        ):
            return f"{package}: an unusable classification without a canary record"
    return None


def _gap_closed(step: Step) -> str | None:
    """The stop state agrees with its decisions; the shape itself is W8's."""
    entries = _owed(step, STOP_PRIMITIVE, lambda package: package.stop)
    if isinstance(entries, str):
        return entries
    for package, entry in entries:
        reference = package.release
        if not isinstance(entry.get("decisions"), list):
            return f"{reference}: no stop decisions"
        decisions = entry["decisions"]
        if not all(isinstance(d, dict) and d.get("action") in STOP_ACTIONS for d in decisions):
            return f"{reference}: a stop decision is malformed"
        gap = _state_gap(reference, entry) or _unusable_gap(reference, entry)
        if gap is not None:
            return gap
    return None


# jev-judge/1 and acceptance-reply/1 --------------------------------------------------------


def _producer_closed(step: Step, judge: Mapping[str, Any]) -> bool:
    """The package that produces the judged criteria closed its stop (an agent outcome)."""
    named = judge.get("criteria")
    package = named.partition(".")[0] if isinstance(named, str) else ""
    entry = (step.record(STOP_PRIMITIVE) or {}).get(package)
    if not isinstance(entry, dict) or entry.get("closed") is not True:
        return False
    decisions = entry.get("decisions")
    acted = _acted([d for d in decisions if isinstance(d, dict)]) if decisions else []
    return bool(acted) and acted[-1].get("action") == "closed"


def _judge_excused(step: Step, judge: Mapping[str, Any]) -> bool:
    """A cut checkpoint (the host-owned ``endings`` marker), or a closed define with no
    criteria, owes no judge pass. A producer that claimed ``done`` is never excused: absent,
    invalid or drifted criteria are a violation (the runner records an integrity error)."""
    if step.cut:
        return True
    closed = judge.get("ending") == NO_CRITERIA and judge.get("criteria_sha256") is None
    return closed and _producer_closed(step, judge)


def _criteria_gap(step: Step, judge: Mapping[str, Any]) -> str | None:
    """The judged criteria are the output the workflow named, at its copied digest."""
    named = judge.get("criteria")
    package, _, output = named.partition(".") if isinstance(named, str) else ("", "", "")
    outputs = (step.record(OUTPUTS_PRIMITIVE) or {}).get(package)
    copied = outputs.get(output) if isinstance(outputs, dict) else None
    recorded = copied.get("sha256") if isinstance(copied, dict) else None
    if not isinstance(recorded, str) or judge.get("criteria_sha256") != recorded:
        return "the judged criteria are not at the digest of their copied output"
    return None


def _judge_shape_gap(judge: Mapping[str, Any]) -> str | None:
    if not isinstance(judge.get("judge"), dict):
        return "no judge settings in the jev-judge/1 record"
    if not isinstance(judge.get("judge_calls"), list) or not isinstance(judge.get("judged"), list):
        return "no judge_calls or judged list in the jev-judge/1 record"
    if judge.get("errors"):
        return f"the jev-judge/1 record reports errors: {judge['errors'][0]}"
    return None


def _baseline_gap(step: Step, judge: Mapping[str, Any]) -> str | None:
    baseline = judge.get("baseline")
    found = isinstance(baseline, dict) and step.file(baseline.get("record")) is not None
    return None if found else "the baseline record file is missing"


def _gap_judge(step: Step) -> str | None:
    judge = step.record(JUDGE_PRIMITIVE) or {}
    gap = _judge_shape_gap(judge)
    if gap is not None:
        return gap
    if _judge_excused(step, judge):
        return None
    return _baseline_gap(step, judge) or _criteria_gap(step, judge) or _gap_judge_pass(step, judge)


def _gap_judge_pass(step: Step, judge: dict[str, Any]) -> str | None:
    if not judge["judged"]:
        return "no judge pass is recorded"
    judging = JudgeStep(step.name, step.agent, judge)
    for read in (_call_evidence, _closure, _undecided):
        _, missing = read(judging)
        if missing:
            return missing[0]
    return None


def _reply_gap(step: Step, number: int, reply: Any) -> str | None:
    if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
        return f"reply {number}: malformed record"
    after = reply.get("after")
    if not isinstance(after, str) or step.file(reply.get("record")) is None:
        return f"reply {number}: its reply record is missing"
    if step.digest(f"{after}/last-message.txt") != reply.get("sha256"):
        return f"reply {number}: the agent's message is not at its recorded digest"
    return None


def _gap_replies(step: Step) -> str | None:
    record = step.record(REPLY_PRIMITIVE) or {}
    replies = record.get("replies")
    if not isinstance(replies, list) or not isinstance(record.get("repairs"), dict):
        return "no replies list or repairs in the acceptance-reply/1 record"
    if not replies and not step.cut:
        return "no reply is recorded"
    return next((gap for n, r in enumerate(replies, 1) if (gap := _reply_gap(step, n, r))), None)


def _gap_judged(step: Step) -> str | None:
    """The reply records, then the judge's own records, of every locked reply or judge."""
    if _locked(step, REPLY_PRIMITIVE) and (gap := _gap_replies(step)) is not None:
        return gap
    return _gap_judge(step) if _locked(step, JUDGE_PRIMITIVE) else None


# integrity ---------------------------------------------------------------------------------


def _integrity_names(source: Mapping[str, Any]) -> list[str]:
    entries = source.get("integrity")
    names: list[str] = []
    for entry in entries if isinstance(entries, list) else []:
        name = entry.get("name") if isinstance(entry, dict) else None
        names.append(str(name or entry))
    return names


def _integrity_sources(step: Step) -> list[Mapping[str, Any]]:
    """The workflow record, the gate record and every turn record that may carry findings."""
    workflow = step.workflow or {}
    turns = workflow.get("turns")
    sources: list[Mapping[str, Any]] = [workflow, _gate(step)]
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


GATE = (GATE_PRIMITIVE,)
ROWS: tuple[Row, ...] = (
    Row("W1", (), "workflow record and each owed primitive record", _gap_workflow),
    Row("W2", (), "session audit of every turn and its native source files", _gap_session),
    Row("W3", GATE, "per-seal gate records", _gap_gates),
    Row("W4", GATE, "the gate ran", _gap_gate_ran),
    Row("W5", GATE, "hook run log", _gap_hook, reading=True),
    Row("W6", GATE, "deterministic setup check record", _gap_setup_recorded),
    Row("W7", GATE, "the setup check passed", _gap_setup_passed),
    Row(
        "W8",
        (SETUP_PRIMITIVE, STOP_PRIMITIVE, OUTPUTS_PRIMITIVE),
        "setup, stop and output call records",
        _gap_calls,
    ),
    Row("W9", (STOP_PRIMITIVE,), "consistent stop state and closed-state records", _gap_closed),
    Row("W10", (JUDGE_PRIMITIVE, REPLY_PRIMITIVE), "reply and judge records", _gap_judged),
    Row("W11", (), "no integrity finding on the hook or session logs", _gap_integrity),
)


def agent_of(config: Any, result: Any) -> Mapping[str, Any] | None:
    """The ``agent`` section of the trial's own ``config.json``, else of ``result.json``'s."""
    for source in (config, result.get("config") if isinstance(result, dict) else None):
        agent = source.get("agent") if isinstance(source, dict) else None
        if isinstance(agent, dict):
            return agent
    return None


def _agent_config(trial_dir: Path) -> Mapping[str, Any] | None:
    config = _json(trial_dir / CONFIG_FILE)
    agent = agent_of(config, None)
    return agent if agent is not None else agent_of(config, _json(trial_dir / RESULT_FILE))


def _definition(agent: Mapping[str, Any]) -> Any:
    kwargs = agent.get("kwargs")
    return kwargs.get("definition") if isinstance(kwargs, dict) else None


def _kwargs(agent: Mapping[str, Any]) -> Mapping[str, Any]:
    kwargs = agent.get("kwargs")
    return kwargs if isinstance(kwargs, dict) else {}


def _declared(parsed: PackageWorkflowDefinition) -> frozenset[str]:
    """The primitives the definition itself declares beyond its packages' contracts."""
    declared = set()
    if parsed.build.reply is not None:
        declared.add(REPLY_PRIMITIVE)
    if parsed.judge is not None:
        declared.add(JUDGE_PRIMITIVE)
    return frozenset(declared)


def _schema_two(definition: Any, kwargs: Mapping[str, Any]) -> Expected:
    """A ``schema: 2`` package workflow, read from the lab's own definition and runner kwargs.

    The runner's ``PackageCheckpoint.record`` always emits ``RECORD_PRIMITIVES`` at the root of
    ``workflow.json``. Every other primitive is owed exactly when the arm's ``primitives`` kwarg
    (the arm's ``primitive_code``: contract id to code digest) lists it. Each listed package must
    also be locked (``locked_packages``, keyed by release name) and leave an ``install/1`` entry
    under its leaf name that names that release. A definition the contract rejects, a package
    workflow whose kwargs omit its locked packages or runner identities, a locked contract id this
    table does not know (an unknown primitive version), or a definition whose reply or judge and
    whose locked reply and judge primitives disagree has unknown applicability."""
    try:
        parsed = PackageWorkflowDefinition.model_validate(definition)
    except ValidationError:
        return _unknown("the workflow definition is malformed")
    found = _read_lock(parsed, kwargs)
    if isinstance(found, str):
        return _unknown(found)
    locked, uses, declared = found
    records = set(RECORD_PRIMITIVES) | (locked & {GATE_PRIMITIVE, REPLY_PRIMITIVE, JUDGE_PRIMITIVE})
    return Expected("workflow", locked, uses, frozenset(records), declared)


def _read_lock(
    parsed: PackageWorkflowDefinition, kwargs: Mapping[str, Any]
) -> tuple[frozenset[str], tuple[tuple[str, str], ...], tuple[Declared, ...]] | str:
    """The locked primitives, packages and declarations of a package arm, or why the lab's
    configuration cannot say what the trial owed."""
    primitives = kwargs.get("primitives")
    locked = frozenset(primitives) if isinstance(primitives, dict) else frozenset()
    if locked - KNOWN_PRIMITIVES:
        unknown = ", ".join(sorted(locked - KNOWN_PRIMITIVES))
        return f"unknown primitive version(s): {unknown}"
    if locked & {REPLY_PRIMITIVE, JUDGE_PRIMITIVE} != _declared(parsed):
        return "the definition and the locked reply and judge primitives disagree"
    uses = tuple((use.release.leaf, use.release.name) for use in parsed.stages)
    if uses and not _runner_kwargs(primitives, kwargs.get("locked_packages"), uses):
        return "the package arm's kwargs omit its locked packages or runner primitives"
    declared = _declarations(kwargs.get("locked_packages"), uses)
    if declared is None:
        return "a locked package carries no readable declared setup, stop and outputs"
    owed = _declared_primitives(declared)
    if owed != locked & {SETUP_PRIMITIVE, STOP_PRIMITIVE, OUTPUTS_PRIMITIVE}:
        return "the packages' declarations and the locked primitives disagree"
    return locked, uses, declared


def _declaration(leaf: str, release: str, entry: Any) -> Declared | None:
    """One package's declared setup, stop and outputs, or ``None`` if missing or malformed."""
    declared = entry.get("declared") if isinstance(entry, dict) else None
    if not isinstance(declared, dict):
        return None
    setup, stop, outputs = (declared.get(k) for k in ("setup", "stop", "outputs"))
    if not isinstance(setup, bool) or not isinstance(stop, bool):
        return None
    if not isinstance(outputs, list) or not all(isinstance(n, str) for n in outputs):
        return None
    return Declared(leaf, release, setup, stop, tuple(sorted(outputs)))


def _declarations(locked: Any, uses: tuple[tuple[str, str], ...]) -> tuple[Declared, ...] | None:
    """Each listed package's declared setup, stop and outputs, as the lab wrote them to the trial
    configuration (``locked_packages[release]["declared"]``) from the very package tree the lock's
    ``stage-tree/1`` digest guards. ``None`` if any is missing or malformed: never inferred."""
    found = []
    for leaf, release in uses:
        entry = locked.get(release) if isinstance(locked, dict) else None
        one = _declaration(leaf, release, entry)
        if one is None:
            return None
        found.append(one)
    return tuple(found)


def _declared_primitives(declared: tuple[Declared, ...]) -> frozenset[str]:
    owed = set()
    for package in declared:
        owed |= {SETUP_PRIMITIVE} if package.setup else set()
        owed |= {STOP_PRIMITIVE} if package.stop else set()
        owed |= {OUTPUTS_PRIMITIVE} if package.outputs else set()
    return frozenset(owed)


def _runner_kwargs(primitives: Any, locked: Any, uses: tuple[tuple[str, str], ...]) -> bool:
    """A package arm's kwargs name the runner's primitives and lock every listed package."""
    if not isinstance(primitives, dict) or not isinstance(locked, dict):
        return False
    return {"frame/1", "install/1", RUNNER_PRIMITIVE} <= set(primitives) and all(
        reference in locked for _, reference in uses
    )


def expected_from(agent: Mapping[str, Any] | None, override: str | None) -> Expected:
    """What a parsed ``agent`` configuration locks; ``UNKNOWN`` when there is none to read."""
    if override:
        return OVERRIDE
    if agent is None:
        return UNKNOWN
    definition = _definition(agent)
    if isinstance(definition, dict) and definition.get("schema") == PACKAGE_SCHEMA:
        return _schema_two(definition, _kwargs(agent))
    return OVERRIDE if agent.get("name") in OVERRIDES else UNKNOWN


def configured(trial_dir: Path, override: str | None) -> Expected:
    """What the lab's own record of the trial locks; ``UNKNOWN`` when it cannot be read."""
    return expected_from(None if override else _agent_config(trial_dir), override)


def _applies(row: Row, expected: Expected) -> bool:
    if not expected.runs_workflow:
        return False
    return not row.primitives or any(p in expected.locked for p in row.primitives)


def _step(trial_dir: Path, name: str, expected: Expected) -> Step:
    agent = trial_dir / "steps" / name / "agent"
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
        return make_check(row.id, gap_status, f"applicability unknown: {expected.why}")
    ruled = [s for s in steps if _applies(row, s.expected)]
    if not ruled:
        owed = " or ".join(row.primitives)
        return make_check(row.id, PASS, f"not applicable: {owed} is not locked ({row.evidence})")
    report = _gap_report(row, ruled)
    if report is not None:
        return make_check(row.id, gap_status, report)
    return make_check(row.id, PASS, f"{row.evidence}: present", steps=[s.name for s in ruled])


def evidence_checks(steps: Sequence[Step], expected: Expected) -> tuple[HealthCheck, ...]:
    """One check per table row over the attempted steps."""
    return tuple(_check(row, steps, expected) for row in ROWS)


def evidence_family(
    trial_dir: Path, override: str | None, attempted: Sequence[str]
) -> tuple[HealthCheck, ...]:
    """One check per table row over the attempted steps; none when no step ran."""
    if not attempted:
        return ()
    expected = configured(trial_dir, override)
    return evidence_checks([_step(trial_dir, name, expected) for name in attempted], expected)
