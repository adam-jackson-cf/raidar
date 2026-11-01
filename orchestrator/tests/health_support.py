"""A finished trial on disk plus its record and job manifest, for run-health audit tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from raidar.contract.snapshot import tree_digest
from raidar.contract.trial import (
    CheckpointState,
    ExecutionValidity,
    PlannedTrial,
    SnapshotInfo,
    StepResult,
    TrialHashes,
    TrialRecord,
    ValidityCheck,
)
from raidar.design.experiment.schema import ProtocolSpec
from raidar.engine.health.base import Audit
from raidar.engine.health.records_manifest import seal_trial
from raidar.engine.trial.collect import TrialContext, classify_trial
from raidar.engine.trial.progression import progression_rule
from raidar.grading.records import (
    BundleRef,
    CoverageRef,
    GraderRef,
    SnapshotRef,
    StepRecord,
    load_step_record,
    step_record_dir,
    write_step_record,
)
from raidar.subject.agent.config import Harness
from tests.execution_support import StepOutcome, verifier_grade, write_trial
from tests.native_support import session_audit

TRIAL_ID = "b00-p0-baseline"
STEP = "checkpoint_1"
CONTINUE = progression_rule(ProtocolSpec())
LIMITS: dict[str, Any] = {
    "agent_s": {"baseline": 900},
    "agent_setup_s": {"baseline": 600},
    "verifier_s": 1200,
    "environment_build_s": 600,
    "multipliers": {"timeout_multiplier": 1.0, "agent_timeout_multiplier": None},
}


@dataclass
class HealthyTrial:
    job_dir: Path
    trial_dir: Path
    record: TrialRecord

    @property
    def job_file(self) -> Path:
        return self.job_dir / "job.json"

    def job(self) -> dict[str, Any]:
        return json.loads(self.job_file.read_text())

    def set_job(self, **changes: Any) -> None:
        job = {**self.job(), **changes}
        self.job_file.write_text(json.dumps(job))

    def write(self, name: str, payload: Any) -> None:
        (self.trial_dir / name).write_text(json.dumps(payload))

    def patch_result(self, edit: Any) -> None:
        path = self.trial_dir / "result.json"
        result = json.loads(path.read_text())
        edit(result)
        path.write_text(json.dumps(result))

    def seal(self) -> None:
        record_file = self.job_dir / "trial-records" / f"{TRIAL_ID}.json"
        digest = seal_trial(self.trial_dir, record_file, self.job_dir)
        self.set_job(records_manifests={TRIAL_ID: digest}, finished_at="2026-01-01T00:00:00+00:00")


def _context(steps: tuple[str, ...]) -> TrialContext:
    hashes = TrialHashes(
        lock="l",
        problem="p",
        arm="a",
        workflow="w",
        packages="s",
        primitive_code="s",
        agent="g",
        protocol="r",
        trial="t",
    )
    trial = PlannedTrial(
        trial_id=TRIAL_ID,
        attempt=0,
        order=0,
        sequence=0,
        arm="baseline",
        problem="scb/adder@fixture",
        problem_index=0,
        steps=steps,
        hashes=hashes,
    )
    return TrialContext(
        trial=trial,
        planned_steps=tuple((step, step) for step in steps),
        task_dir=Path.cwd(),
        provisions=(),
        pinned_harness_version="0.156.1",
        rule=CONTINUE,
    )


def _store_snapshot(trial_dir: Path, step: str) -> None:
    """A real stored ``/app`` tree, and the verifier ledger pointed at its true digest."""
    app = trial_dir / "steps" / step / "artifacts" / "app"
    app.mkdir(parents=True)
    (app / "main.py").write_text("print('hello')\n")
    record_dir = step_record_dir(trial_dir, step)
    ledger = load_step_record(record_dir)
    snapshot = SnapshotRef(
        requested="artifact",
        used="artifact",
        digest=tree_digest(app),
        stored_path=f"steps/{step}/artifacts/app",
    )
    write_step_record(record_dir, ledger.model_copy(update={"snapshot": snapshot}))


def write_agent_session(trial_dir: Path, step: str, rows: list[str] | None = None) -> Path:
    """A one-turn workflow record and its codex rollout (``rows``: message texts) for a step."""
    agent = trial_dir / "steps" / step / "agent"
    sessions = agent / "workflow" / "build-1" / "sessions" / "2026" / "10" / "26"
    sessions.mkdir(parents=True, exist_ok=True)
    turns = [{"stage": "build", "turn": 1, "log_dir": "workflow/build-1"}]
    lines = [
        json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"text": text}]},
            }
        )
        for text in (rows or ["solve the task"])
    ]
    rollout = sessions / "rollout-2026-10-26T00-00-00-abc.jsonl"
    rollout.write_text("\n".join(lines) + "\n")
    turns[0]["session_audit"] = session_audit(Harness.CODEX_CLI, agent / "workflow" / "build-1")
    record = {"kind": "raidar-workflow", "stages": ["build"], "turns": turns}
    # what a no-package schema-2 run's PackageCheckpoint.record emits (empty: no package)
    record |= {c: {} for c in ("frame/1", "install/1", "setup/1", "stop/1", "outputs/1")}
    (agent / "workflow" / "workflow.json").write_text(json.dumps(record))
    return rollout


def _write_evidence(trial_dir: Path) -> None:
    point = {"present": True, "sha256": "a" * 64, "entries": 3, "measured": True, "step": STEP}
    hashes = {"points": [{**point, "point": "baseline"}, {**point, "point": "after_agent"}]}
    (trial_dir / "raidar-tool-hashes.json").write_text(json.dumps(hashes))
    phase = {
        "step": STEP,
        "decision": "active",
        "sample_count": 4,
        "unmeasured_samples": 0,
        "first_idle_at_s": None,
        "reason": "no idle window reached",
    }
    (trial_dir / "raidar-liveness.json").write_text(json.dumps({"phases": [phase]}))


BASELINE_DEFINITION: dict[str, Any] = {
    "schema": 2,
    "name": "one-shot",
    "build": {"prompt": "build %%%TASK%%%", "reply": None},
    "stages": [],
    "gate": {"max_fix_rounds": 0},
    "judge": None,
}
"""The real ``schema: 2`` definition of a package-free arm (``PackageWorkflowDefinition``)."""


def write_trial_config(
    trial_dir: Path, definition: dict[str, Any] | None = None, **kwargs: Any
) -> None:
    """The lab's own ``config.json``: the resolved definition and the runner kwargs that
    ``arm_agent_config`` adds for a package arm (``locked_packages``, ``primitives``, ``pack``)."""
    body = {"definition": definition or BASELINE_DEFINITION, **kwargs}
    (trial_dir / "config.json").write_text(json.dumps({"agent": {"kwargs": body}}))


def healthy_trial(
    tmp_path: Path, outcomes: list[StepOutcome] | None = None, *, job_dir: Path | None = None
) -> HealthyTrial:
    job_dir = job_dir or tmp_path / "job"
    trial_dir = job_dir / "trials" / TRIAL_ID
    outcomes = outcomes or [StepOutcome(STEP)]
    write_trial(trial_dir, outcomes)
    write_trial_config(trial_dir)
    for outcome in outcomes:
        if outcome.passed is not None:
            _store_snapshot(trial_dir, outcome.name)
        write_agent_session(trial_dir, outcome.name)
    steps = tuple(outcome.name for outcome in outcomes)
    step_results = classify_trial(trial_dir, _context(steps))
    record = TrialRecord(
        experiment="exp",
        job_id="j",
        trial=_context(steps).trial,
        trial_dir=f"trials/{TRIAL_ID}",
        progression=CONTINUE,
        step_results=step_results,
        reached=tuple(False for _ in steps),
        reached_source="derived",
        validity=ExecutionValidity(checks=(ValidityCheck(name="trial_completed", passed=True),)),
    )
    (job_dir / "trial-records").mkdir(parents=True)
    (job_dir / "trial-records" / f"{TRIAL_ID}.json").write_text(record.model_dump_json())
    _write_evidence(trial_dir)
    manifest = {
        "kind": "raidar-job",
        "runner": {"commit": "abc", "dirty": False, "toolchain_sha256": "t" * 64},
        "effective_limits": LIMITS,
        "concurrency": 1,
        "execution_target": {"name": "mbp", "effective_concurrency": 1, "docker": {"ncpu": 4}},
        "selected": [TRIAL_ID],
    }
    (job_dir / "job.json").write_text(json.dumps(manifest))
    return HealthyTrial(job_dir, trial_dir, record)


# ---- the lab-owned records of a locked package workflow (health family W) ----------------------

PACKAGES = ("define", "verify")
BASE_PRIMITIVES = ("frame/1", "install/1", "runner/1")
CRITERIA_FILE = "stages/raidar_define/outputs/close/criteria/criteria.json"
DEFINITION_FILE = "stages/raidar_define/outputs/close/definition/feature-definition.md"
LAST_MESSAGE = "workflow/build-1/last-message.txt"
DECLARED = {
    "define": {"setup": True, "stop": True, "outputs": ["criteria", "definition"]},
    "verify": {"setup": False, "stop": False, "outputs": []},
}
"""What ``agent_config`` writes per locked package from its manifest (``raidar/define`` declares
setup, stop and the outputs ``criteria`` and ``definition``)."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _put(agent: Path, relative: str, text: str) -> str:
    path = agent / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return _sha(text)


def _call(package: str, primitive: str) -> dict[str, Any]:
    """The host's ``call/1`` record of one package call."""
    return {
        "schema": "call/1",
        "package": f"raidar/{package}",
        "primitive": primitive,
        "sequence": 1,
        "ran": True,
        "exit_code": 0,
        "signal": None,
        "timed_out": False,
        "overflow": False,
        "bounds_violation": None,
        "integrity_error": None,
    }


def _invocation(package: str, primitive: str) -> dict[str, Any]:
    return {"call": _call(package, primitive), "bounds_violation": None, "state": {}}


def _output(agent: Path, name: str, relative: str, contract: str | None) -> dict[str, Any]:
    digest = _put(agent, relative, f"{name}\n")
    return {
        "path": f"/app/.raidar/{name}",
        "contract": contract,
        "sha256": digest,
        "bytes": len(name) + 1,
        "host_path": relative,
        "error": None,
    }


def _outputs(agent: Path) -> dict[str, Any]:
    """The copy record of each output ``raidar/define``'s manifest declares."""
    return {
        "criteria": _output(agent, "criteria", CRITERIA_FILE, "criteria/1"),
        "definition": _output(agent, "definition", DEFINITION_FILE, None),
    }


def _stop_entry(outputs: dict[str, Any]) -> dict[str, Any]:
    resume = {
        "action": "resume",
        "reason": "requested",
        "call": _invocation("define", "stop/1"),
        "status": "resume",
        "outputs": outputs,
    }
    done = resume | {"action": "done", "reason": "reported", "status": "done"}
    digests = {name: output["sha256"] for name, output in outputs.items()}
    run = {
        "label": "initial",
        "passed": True,
        "expected": {"stdout_sha256": "s" * 64, "outputs": digests},
        "actual": {"stdout_sha256": "s" * 64, "outputs": digests},
        "call": {},
    }
    return {
        "rounds": 1,
        "repaired": False,
        "closed": False,
        "carry": {},
        "tokens": {},
        "canary": [run],
        "decisions": [resume, done],
    }


def _gate_record(agent: Path) -> dict[str, Any]:
    _put(agent, "gate/gate-1.json", json.dumps({"verdict": "passed"}))
    _put(agent, "gate/hook-runs.jsonl", json.dumps({"verdict": "passed", "tree": "t"}) + "\n")
    return {
        "max_fix_rounds": 0,
        "base": "b",
        "hooks_path": "/hooks",
        "canary": {"verdict": "passed"},
        "setup_check": {"status": "passed", "checks": []},
        "integrity": [],
        "reaps": [],
        "pre_fix_snapshots": [],
        "hook_outputs": [],
        "gates": [
            {
                "verdict": "passed",
                "tree": "t",
                "attribution": "passed",
                "record": "gate/gate-1.json",
                "checks": [],
            }
        ],
        "errors": [],
    }


def _judge_files(agent: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    result = {"ok": True, "answers": {"aspect_asserted": {"type": "noul", "noul": 0.95}}}
    for name, document in (("request", {"state": {}}), ("response", result), ("result", result)):
        _put(agent, f"workflow/judge/call-0.{name}.json", json.dumps(document))
    call = {
        "index": 0,
        "aspect": "F1.AC1.1",
        "probability": 0.95,
        "result": result,
        "request_file": "workflow/judge/call-0.request.json",
        "response_file": "workflow/judge/call-0.response.json",
        "result_file": "workflow/judge/call-0.result.json",
    }
    aspect = {"id": "F1.AC1.1", "calls": [0], "probability": 0.95, "status": "closed"}
    criterion = {
        "id": "F1.AC1",
        "tests": ["t"],
        "failing": [],
        "test_results": {"t": "passed"},
        "aspects": [aspect],
        "status": "closed",
    }
    passed = {
        "pass": 1,
        "gate_verdict": "passed",
        "criteria": [criterion],
        "closed": ["F1.AC1"],
        "open": [],
        "undecided": [],
    }
    return {"open_at": 0.3, "closed_at": 0.8}, passed, [call]


def _judge_record(agent: Path, criteria_sha256: str) -> dict[str, Any]:
    settings, passed, calls = _judge_files(agent)
    _put(agent, "gate/baseline.json", "{}\n")
    return {
        "criteria": "define.criteria",
        "criteria_sha256": criteria_sha256,
        "model": "m",
        "judge": settings,
        "passes": 1,
        "max_passes": 3,
        "judged": [passed],
        "judge_calls": calls,
        "baseline": {"record": "gate/baseline.json"},
        "baseline_passed": [],
        "ending": "verified",
        "errors": [],
    }


def _reply_record(agent: Path) -> dict[str, Any]:
    digest = _put(agent, LAST_MESSAGE, "done\n")
    _put(agent, "workflow/build-1/reply.json", "{}\n")
    reply = {
        "after": "workflow/build-1",
        "kind": "first_build",
        "ok": True,
        "fault": None,
        "error": None,
        "dropped": [],
        "reported": [],
        "sha256": digest,
        "record": "workflow/build-1/reply.json",
    }
    return {
        "replies": [reply],
        "repairs": {"syntax": 0, "semantic": 0},
        "ac_tests": {},
        "adrs": [],
        "sources": None,
    }


def package_definition(*, judge: bool, packages: tuple[str, ...]) -> dict[str, Any]:
    """The real ``schema: 2`` definition of a package arm."""
    body: dict[str, Any] = {
        "schema": 2,
        "name": "define-build-verify",
        "build": {"prompt": "build %%%TASK%%%", "reply": "acceptance-reply/1" if judge else None},
        "stages": [{"use": f"raidar/{name}@1", "params": {}} for name in packages],
        "gate": {"max_fix_rounds": 0},
        "judge": None,
    }
    if judge:
        body["judge"] = {
            "criteria": "define.criteria",
            "model": "m",
            "closed_at": 0.8,
            "open_at": 0.3,
            "max_passes": 3,
            "rework": "rework %%%GATE%%% %%%PUSHBACK%%%",
        }
    return body


def package_kwargs(*, gate: bool = True, calls: bool = True, judge: bool = True) -> dict[str, Any]:
    """The runner kwargs of a package arm locking the chosen primitives. Without ``calls`` the arm
    runs only the package that declares no setup, stop or outputs."""
    packages = PACKAGES if calls else ("verify",)
    extra = [
        *(["gate/1"] if gate else []),
        *(["setup/1", "stop/1", "outputs/1"] if calls else []),
        *(["jev-judge/1", "acceptance-reply/1"] if judge else []),
    ]
    locked = {
        f"raidar/{name}": {
            "release": f"raidar/{name}@1",
            "declared": {**DECLARED[name], "outputs": list(DECLARED[name]["outputs"])},
        }
        for name in packages
    }
    return {
        "definition": package_definition(judge=judge, packages=packages),
        "locked_packages": locked,
        "primitives": dict.fromkeys((*BASE_PRIMITIVES, *extra), "d" * 64),
        "pack": "p",
    }


def write_package_config(trial_dir: Path, **locked: bool) -> None:
    """The lab's ``config.json`` of a package arm locking the chosen primitives."""
    kwargs = package_kwargs(**locked)
    write_trial_config(trial_dir, kwargs.pop("definition"), **kwargs)


def package_records(
    agent: Path, *, gate: bool = True, calls: bool = True, judge: bool = True
) -> dict[str, Any]:
    """Every primitive record the locked primitives leave at the root of ``workflow.json`` (and
    the files they name under ``agent``), as a real package trial would."""
    packages = PACKAGES if calls else ("verify",)
    outputs = _outputs(agent)
    records: dict[str, Any] = {
        "frame/1": dict.fromkeys(packages, "fragment"),
        "install/1": {
            name: {"schema": "install/1", "reference": f"raidar/{name}", "package_digest": "a" * 64}
            for name in packages
        },
        "setup/1": {},
        "stop/1": {},
        "outputs/1": {},
    }
    if calls:
        records["setup/1"] = {"define": {**_invocation("define", "setup/1"), "outputs": {}}}
        records["stop/1"] = {"define": _stop_entry(outputs)}
        records["outputs/1"] = {"define": {**outputs, "cut": None}}
    if gate:
        records["gate/1"] = _gate_record(agent)
    if judge:
        records["jev-judge/1"] = _judge_record(agent, outputs["criteria"]["sha256"])
        records["acceptance-reply/1"] = _reply_record(agent)
    return records


def package_turn(agent: Path) -> dict[str, Any]:
    """One build turn whose audit names a native source file written under ``agent``."""
    digest = _put(agent, "workflow/build-1/sessions/rollout.jsonl", '{"type": "event_msg"}\n')
    audit = {"records": 1, "sources": [{"path": "sessions/rollout.jsonl", "sha256": digest}]}
    return {"stage": "build", "turn": 1, "log_dir": "workflow/build-1", "session_audit": audit}


# ---- audit inputs built in memory: no run directory, ledger or result file is written -----------

SNAPSHOT_DIGEST = "d" * 64
START = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


def memory_ledger(
    step: str = STEP,
    *,
    passed: bool = True,
    infra: str | None = None,
    digest: str = SNAPSHOT_DIGEST,
    stored_path: str | None = None,
) -> StepRecord:
    """A step's verifier ledger as the verifier would have stored it (``infra``: failure code)."""
    grade = verifier_grade(step, StepOutcome(step, passed=passed, infra=infra))
    return StepRecord(
        step=step,
        checkpoint=step,
        bundle=BundleRef(root="bundle", digest="b" * 64, problem="scb/adder@fixture"),
        snapshot=SnapshotRef(
            requested="artifact",
            used="artifact",
            digest=digest,
            stored_path=stored_path or f"steps/{step}/artifacts/app",
        ),
        grader=GraderRef(image="grader:x", environment_id="env", image_built=False, timeout_s=900),
        grade=grade,
        summary=grade.summary(),
        coverage=CoverageRef(enabled=False),
        rewards=grade.rewards(),
    )


def memory_step(step: str = STEP, ledger: StepRecord | None = None, **changes: Any) -> StepResult:
    """The trial record's result of a step whose ledger agrees with it (``changes`` break that)."""
    ledger = ledger or memory_ledger(step)
    fields: dict[str, Any] = {
        "trial_id": TRIAL_ID,
        "arm": "baseline",
        "attempt": 0,
        "problem": "scb/adder@fixture",
        "index": 0,
        "step": step,
        "checkpoint": step,
        "checkpoint_state": (
            CheckpointState.SOLVED if ledger.grade.solved else CheckpointState.FAILED
        ),
        "strict_pass_rate": ledger.grade.pass_rate(),
        "rewards": ledger.rewards,
        "snapshot": SnapshotInfo(path=ledger.snapshot.stored_path, digest=ledger.snapshot.digest),
    }
    return StepResult(**{**fields, **changes})


def memory_trial(steps: tuple[StepResult, ...], **changes: Any) -> TrialRecord:
    """A trial record over ``steps`` that only holds the fields the audit reads."""
    names = tuple(s.step for s in steps)
    fields: dict[str, Any] = {
        "experiment": "exp",
        "job_id": "j",
        "trial": _context(names).trial,
        "trial_dir": f"trials/{TRIAL_ID}",
        "progression": CONTINUE,
        "step_results": steps,
        "reached": tuple(False for _ in steps),
        "reached_source": "derived",
        "validity": ExecutionValidity(checks=(ValidityCheck(name="trial_completed", passed=True),)),
    }
    return TrialRecord(**{**fields, **changes})


def _span(start_s: float, end_s: float) -> dict[str, str]:
    return {
        "started_at": (START + timedelta(seconds=start_s)).isoformat(),
        "finished_at": (START + timedelta(seconds=end_s)).isoformat(),
    }


def memory_result(
    *steps: str, setup_s: float = 1.0, agent_s: float = 10.0, verify_s: float = 5.0
) -> dict[str, Any]:
    """Harbor's ``result.json`` body: agent setup, then per step an agent phase and a verifier."""
    clock = setup_s
    items = []
    for step in steps:
        agent = _span(clock, clock + agent_s)
        verifier = _span(clock + agent_s, clock + agent_s + verify_s)
        items.append({"step_name": step, "agent_execution": agent, "verifier": verifier})
        clock += agent_s + verify_s
    return {"agent_setup": _span(0.0, setup_s), "step_results": items}


def memory_job(**changes: Any) -> dict[str, Any]:
    """A job manifest that records every fact the audit reads, as a healthy run's does."""
    manifest: dict[str, Any] = {
        "kind": "raidar-job",
        "runner": {"commit": "abc", "dirty": False, "toolchain_sha256": "t" * 64},
        "effective_limits": LIMITS,
        "concurrency": 1,
        "execution_target": {"name": "mbp", "effective_concurrency": 1, "docker": {"ncpu": 4}},
        "selected": [TRIAL_ID],
    }
    return {**manifest, **changes}


def memory_audit(
    trial_dir: Path,
    *,
    ledgers: dict[str, StepRecord | None] | None = None,
    result: dict[str, Any] | None = None,
    job: dict[str, Any] | None = None,
    record: TrialRecord | None = None,
) -> Audit:
    """A one-step audit over in-memory evidence; ``trial_dir`` is only where host files would be.

    Defaults describe a healthy ``checkpoint_1``; each argument replaces one piece wholesale.
    """
    ledgers = {STEP: memory_ledger()} if ledgers is None else ledgers
    record = record or memory_trial((memory_step(STEP, ledgers.get(STEP)),))
    result = memory_result(*(s.step for s in record.step_results)) if result is None else result
    job = memory_job() if job is None else job
    job_dir = trial_dir.parent.parent  # <job>/trials/<trial>
    return Audit(job_dir, trial_dir, record, job, result=result, ledgers=ledgers)
