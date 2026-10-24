"""A finished trial on disk plus its record and job manifest, for run-health audit tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.contract.snapshot import tree_digest
from raidar.design.experiment.arm import compile_condition
from raidar.design.experiment.schema import AgentEntry, ConditionSpec, ProtocolSpec
from raidar.engine.health.records_manifest import seal_trial
from raidar.engine.trial.collect import TrialContext, classify_trial
from raidar.engine.trial.models import (
    ExecutionValidity,
    PlannedTrial,
    TrialHashes,
    TrialRecord,
    ValidityCheck,
)
from raidar.engine.trial.progression import progression_rule
from raidar.grading.records import (
    SnapshotRef,
    load_step_record,
    step_record_dir,
    write_step_record,
)
from tests.execution_support import StepOutcome, write_trial

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
    agent = AgentEntry(id="luna", name="codex", provider="openai", model="m", version="0.156.1")
    hashes = TrialHashes(
        lock="l", problem="p", arm="a", condition="c", agent="g", protocol="r", trial="t"
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
        compiled=compile_condition(ConditionSpec(id="baseline"), agent, base_dir=Path.cwd()),
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


def healthy_trial(
    tmp_path: Path, outcomes: list[StepOutcome] | None = None, *, job_dir: Path | None = None
) -> HealthyTrial:
    job_dir = job_dir or tmp_path / "job"
    trial_dir = job_dir / "trials" / TRIAL_ID
    outcomes = outcomes or [StepOutcome(STEP)]
    write_trial(trial_dir, outcomes)
    for outcome in outcomes:
        if outcome.passed is not None:
            _store_snapshot(trial_dir, outcome.name)
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
