"""Synthetic new-model fixture for the Quality element (review-surface development and tests).

Writes, under one directory, clearly labelled synthetic data in the real
job, Assessment and Jev shapes:

- ``experiment.yaml``, a digest-checked ``experiment.lock.json`` beside it
  (baseline arm and headline metric, as a real lock carries them), and
  ``jobs/<experiment>/<job>/`` job directories (``job.json`` naming the
  lock digest + ``trial-records/*.json`` TrialRecords);
  ``store/``: ``code-quality@9`` and ``correctness@10`` Assessments whose
  inputs carry each trial's snapshot digests (correctness in the real
  scorer's shape: per-checkpoint ``solved`` and ``strict_pass_rate``, and
  trial ``solved_checkpoints`` and ``reached``), plus a Jev audit of
  rubric decisions for the label-export flow.

The experiment id, job ids, problem and every snapshot digest carry a
``synthetic`` marker, and the projection built from it sets
``synthetic: true``. It is never benchmark evidence.

- the gated arm's gate/1 records (``workflow/workflow.json``) under each trial's step agent
  dirs (``tests.review_fixtures.quality.gate``) and every trial's ``verification-effort``
  Assessment, made by the real scorer (``not_applicable`` for ``baseline``).

Built-in shape (deterministic, seeded): two arms (``baseline``, ``guided``)
x 6 attempts x one 5-checkpoint problem. ``guided`` runs behind a commit gate
and is better on E and R;
attempt 4 of ``guided`` is an invalid trial (infra-excluded); attempt 2 of
``baseline`` has an ineligible H at checkpoint 3, so its headline S is
ineligible with reasons.

``synthetic_quality`` builds the same design in memory (trials, Assessments under made-up
keys, Jev decisions, gate records) with nothing written; the fast tests read that, and a slow
seam test shows the on-disk fixture projects to the same Quality view.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from raidar.contract.trial import (
    CheckpointState,
    ExecutionValidity,
    PlannedTrial,
    ProgressionRule,
    SnapshotInfo,
    StepResult,
    TrialHashes,
    TrialRecord,
    ValidityCheck,
)
from raidar.engine.job.assessment import lock_rescore_config
from raidar.evaluation.analysis.sources import read_lock
from raidar.evaluation.review.quality.labels import JevAudit
from raidar.evaluation.review.quality.sources import ExperimentInfo, TrialSource
from raidar.scoring.framework.builtin_manifests import CODE_QUALITY, CORRECTNESS
from raidar.scoring.framework.result import (
    AssessResult,
    AssessStatus,
    Evidence,
    Observation,
    Span,
    transition_subject,
)
from raidar.scoring.framework.store import Assessment, AssessmentStore, ScorerIdentity
from raidar.scoring.judge.audit import (
    AuditLog,
    DecisionRecord,
    DecisionStage,
    InputSufficiency,
    SufficiencyLevel,
)
from raidar.scoring.judge.policies import PolicyMode
from raidar.scoring.judge.rubric import RUBRIC_POLICY
from raidar.scoring.verification_effort.records import StepGate
from tests.review_fixtures.projection.trials import write_bundle
from tests.review_fixtures.quality.gate import (
    VERIFICATION_EFFORT,
    gate_records,
    gate_result,
    memory_gate_result,
    parse_gate,
)
from tests.review_fixtures.quality.inputs import write_inputs
from tests.review_fixtures.quality.lock import (
    default_metric,
    synthetic_analysis,
    write_synthetic_lock,
)
from tests.review_fixtures.quality.store import FixtureAssessments

EXPERIMENT = "synthetic-quality"
PROBLEM = "synthetic/ledger@0"
RUN_ID = "synthetic-run-1"
ARMS = ("baseline", "guided")
BLOCKS = 6
CHECKPOINTS = tuple(f"checkpoint_{index}" for index in range(1, 6))
INVALID = ("guided", 4)
GATED_ARM = "guided"
INELIGIBLE_H = ("baseline", 2, "checkpoint_3")
_SEED = 20260930
_TIMESTAMP = "2026-09-30T00:00:00.000Z"
_FILES = ("ledger/core.py", "ledger/report.py", "ledger/cli.py")


def _digest(*parts: object) -> str:
    return hashlib.sha256("synthetic|".join(map(str, parts)).encode()).hexdigest()


@dataclass
class _Trial:
    arm: str
    attempt: int
    rng: random.Random
    observations: list[Observation] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    correctness: list[Observation] = field(default_factory=list)

    @property
    def trial_id(self) -> str:
        return f"b{self.attempt:02d}-p0-{self.arm}"

    def snapshot(self, checkpoint: str) -> str:
        return _digest(EXPERIMENT, self.trial_id, checkpoint)

    def ok(self, metric: str, subject: str, value: float) -> None:
        self.observations.append(
            Observation(metric=metric, subject=subject, status=AssessStatus.OK, value=value)
        )

    def item(
        self, metric: str, subject: str, kind: str, line: int, length: int, message: str
    ) -> None:
        path = self.rng.choice(_FILES)
        self.evidence.append(
            Evidence(
                metric=metric,
                subject=subject,
                path=path,
                sha256=_digest(self.trial_id, subject, path),
                kind=kind,
                spans=(Span(line=line, end_line=line + length - 1),),
                message=message,
            )
        )


def _architecture(trial: _Trial, checkpoint: str) -> float | None:
    if (trial.arm, trial.attempt, checkpoint) == INELIGIBLE_H:
        trial.observations.append(
            Observation(
                metric="architecture",
                subject=checkpoint,
                status=AssessStatus.INELIGIBLE,
                reason="entry_file_absent: ledger/cli.py is not a file in the snapshot",
            )
        )
        return None
    architecture = 1.0 if trial.rng.random() < 0.7 else round(trial.rng.uniform(0.8, 0.98), 4)
    trial.ok("architecture", checkpoint, architecture)
    if architecture < 1:
        trial.item(
            "architecture",
            checkpoint,
            "cyclic_call",
            5,
            12,
            "report -> core (weight 2) is in a call cycle",
        )
    return architecture


def _findings(trial: _Trial, checkpoint: str, lint: int, high_cc: int) -> None:
    for number in range(lint):
        code = trial.rng.choice(("SIM102", "SIM108", "SIM118"))
        trial.item(
            "verbosity",
            checkpoint,
            f"ruff:{code}",
            10 + 17 * number,
            1,
            f"Ruff {code} (SCB contract rule set)",
        )
    for number in range(high_cc):
        trial.item(
            "erosion",
            checkpoint,
            "high_complexity",
            20 + 100 * number,
            trial.rng.randint(25, 80),
            f"function handle_{number}: cc={11 + number} (synthetic)",
        )


def _checkpoint(trial: _Trial, index: int, checkpoint: str) -> dict[str, float | None]:
    rng = trial.rng
    lift = 1.0 if trial.arm == "guided" else 0.0
    loc = 180 + 140 * index + rng.randint(-20, 20)
    high_cc = max(0, index + rng.randint(-1, 1) - int(lift))
    erosion = max(0.05, 0.85 - 0.09 * index + 0.08 * lift + rng.uniform(-0.03, 0.03))
    lint = max(0, rng.randint(0, 2 + index) - int(lift and index))
    verbosity = 1 - (lint * 3 + rng.randint(0, 6)) / loc
    trial.ok("verbosity", checkpoint, verbosity)
    trial.ok("erosion", checkpoint, erosion)
    trial.ok("lint_findings", checkpoint, float(lint))
    trial.ok("production_loc", checkpoint, float(loc))
    _findings(trial, checkpoint, lint, high_cc)
    architecture = _architecture(trial, checkpoint)
    solved = rng.random() < 0.8 - 0.1 * index + 0.15 * lift
    rate = 1.0 if solved else round(rng.uniform(0.55, 0.97), 4)
    trial.correctness.extend(
        Observation(metric=metric, subject=checkpoint, status=AssessStatus.OK, value=value)
        for metric, value in (("solved", float(solved)), ("strict_pass_rate", rate))
    )
    return {"verbosity": verbosity, "erosion": erosion, "architecture": architecture}


def _transition(trial: _Trial, subject: str) -> dict[str, float]:
    rng, guided = trial.rng, trial.arm == "guided"
    rework = min(1.0, max(0.0, 0.6 + (0.15 if guided else 0.0) + rng.uniform(-0.1, 0.1)))
    regression = 1.0 if rng.random() < 0.6 else round(rng.uniform(0.7, 0.95), 4)
    trial.ok("rework", subject, rework)
    trial.ok("regression", subject, regression)
    trial.ok("regression_parity_excluded_cases", subject, 0.0)
    if rework < 1:
        trial.item(
            "rework",
            subject,
            "repeated_rework:added",
            30,
            6,
            "6 added lines re-modify symbols already changed earlier",
        )
    if regression < 1:
        trial.item(
            "regression",
            subject,
            "regression_attribution",
            44,
            3,
            "post_entry changed and is executed by failing regression(s)",
        )
    return {"rework": rework, "regression": regression}


def _problem_means(trial: _Trial, series: dict[str, list[float | None]]) -> None:
    for metric, values in series.items():
        name = f"problem_{metric}"
        present = [value for value in values if value is not None]
        if len(present) == len(values):
            trial.ok(name, "trajectory", sum(present) / len(present))
        else:
            trial.observations.append(
                Observation(
                    metric=name,
                    subject="trajectory",
                    status=AssessStatus.INELIGIBLE,
                    reason=f"no value for {INELIGIBLE_H[2]} (ineligible)",
                )
            )


def _measure(trial: _Trial) -> None:
    series: dict[str, list[float | None]] = {
        "verbosity": [], "erosion": [], "architecture": [], "rework": [], "regression": []
    }  # fmt: skip
    for index, checkpoint in enumerate(CHECKPOINTS):
        for metric, value in _checkpoint(trial, index, checkpoint).items():
            series[metric].append(value)
        if index:
            subject = transition_subject(CHECKPOINTS[index - 1], checkpoint)
            for metric, value in _transition(trial, subject).items():
                series[metric].append(value)
    _problem_means(trial, series)
    solved = [item.value == 1.0 for item in trial.correctness if item.metric == "solved"]
    reached = next((index for index, value in enumerate(solved) if not value), len(solved))
    totals = {"solved_checkpoints": float(sum(solved)), "reached": float(reached)}
    trial.correctness.extend(
        Observation(metric=metric, subject="trajectory", status=AssessStatus.OK, value=value)
        for metric, value in totals.items()
    )


def _record(trial: _Trial) -> TrialRecord:
    valid = (trial.arm, trial.attempt) != INVALID
    step_results = tuple(
        StepResult(
            trial_id=trial.trial_id,
            arm=trial.arm,
            attempt=trial.attempt,
            problem=PROBLEM,
            index=index,
            step=checkpoint,
            checkpoint=checkpoint,
            checkpoint_state=CheckpointState.SOLVED
            if observation.value == 1.0
            else CheckpointState.FAILED,
            strict_pass_rate=observation.value,
            snapshot=SnapshotInfo(
                path=f"steps/{checkpoint}/artifacts/app", digest=trial.snapshot(checkpoint)
            ),
            trace_dir=f"steps/{checkpoint}/agent",
        )
        for index, (checkpoint, observation) in enumerate(
            zip(
                CHECKPOINTS,
                [item for item in trial.correctness if item.metric == "strict_pass_rate"],
                strict=True,
            )
        )
    )
    hashes = {
        name: _digest(name, trial.trial_id)
        for name in (
            "lock",
            "problem",
            "arm",
            "workflow",
            "packages",
            "primitive_code",
            "agent",
            "protocol",
            "trial",
        )
    }
    return TrialRecord(
        experiment=EXPERIMENT,
        job_id=RUN_ID,
        trial=PlannedTrial(
            trial_id=trial.trial_id,
            attempt=trial.attempt,
            order=ARMS.index(trial.arm),
            sequence=trial.attempt * len(ARMS) + ARMS.index(trial.arm),
            arm=trial.arm,
            problem=PROBLEM,
            problem_index=0,
            steps=CHECKPOINTS,
            hashes=TrialHashes(**hashes),
        ),
        trial_dir=f"trials/{trial.trial_id}",
        progression=ProgressionRule(mode="continue", reward_key="strict_pass_rate"),
        step_results=step_results,
        reached=tuple(True for _ in CHECKPOINTS),
        reached_source="derived",
        validity=ExecutionValidity(
            checks=(
                ValidityCheck(
                    name="steps_started",
                    passed=valid,
                    detail="" if valid else "synthetic: agent container exited before checkpoint_1",
                ),
            )
        ),
    )


def _decision_records(trial: _Trial) -> list[DecisionRecord]:
    if trial.attempt > 1:
        return []
    records = []
    for number, checkpoint in enumerate(CHECKPOINTS[:3]):
        flagged = (number + trial.attempt) % 2 == 0
        records.append(
            DecisionRecord(
                timestamp=_TIMESTAMP,
                request_id=f"jevp_synthetic-{trial.trial_id}-{number}",
                policy=RUBRIC_POLICY,
                policy_version="1",
                mode=PolicyMode.SHADOW,
                question_set="scb-rubric-smells@1",
                stage=DecisionStage.JEV,
                subject=f"{checkpoint}:ledger/core.py:post_entry",
                trial=trial.trial_id,
                answers={"q_smell": {"type": "noul", "noul": 0.8 if flagged else 0.2}},
                input_sufficiency=InputSufficiency(level=SufficiencyLevel.SUFFICIENT),
                verdict="flag" if flagged else "pass",
                enforced=False,
                counts_toward_verdicts=False,
            )
        )
    return records


def _designs() -> Iterator[_Trial]:
    """The design's trials with their measurements drawn, in the order they were run."""
    rng = random.Random(_SEED)
    for attempt in range(BLOCKS):
        for arm in ARMS:
            trial = _Trial(arm=arm, attempt=attempt, rng=random.Random(rng.random()))
            _measure(trial)
            yield trial


def generate_quality_fixture(dest: Path) -> Path:
    """Write the synthetic runs, experiment file and store under ``dest`` (replaced)."""
    shutil.rmtree(dest, ignore_errors=True)
    job_dir = dest / "jobs" / EXPERIMENT / RUN_ID
    (job_dir / "trial-records").mkdir(parents=True)
    experiment_file = dest / "experiment.yaml"
    experiment_file.write_text(
        f"# synthetic fixture: not benchmark evidence\nid: {EXPERIMENT}\n"
        "analysis:\n  baseline_arm: baseline\n  attempts: 6\n  seed: 20260930\n"
        "assessment:\n  scorers: [correctness@10, code-quality@9, verification-effort@1]\n",
        encoding="utf-8",
    )
    lock_sha256 = write_synthetic_lock(
        experiment_file,
        experiment=EXPERIMENT,
        baseline_arm=ARMS[0],
        metric=default_metric(len(CHECKPOINTS)),
        problem=PROBLEM,
        checkpoints=len(CHECKPOINTS),
    )
    job_dir.joinpath("job.json").write_text(
        json.dumps(
            {
                "kind": "raidar-job",
                "synthetic": True,
                "experiment": EXPERIMENT,
                "experiment_file": str(experiment_file),
                "lock_sha256": lock_sha256,
                "job_id": RUN_ID,
                "finished_at": _TIMESTAMP,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    store = AssessmentStore(dest / "store")
    settings = lock_rescore_config(read_lock(dest / "experiment.lock.json"))
    assessments = FixtureAssessments(settings)
    bundle = write_bundle(dest / "bundle", PROBLEM, CHECKPOINTS)
    audit = AuditLog.for_store(dest / "store")
    for trial in _designs():
        record = write_inputs(job_dir, _record(trial), bundle)
        (job_dir / "trial-records" / f"{trial.trial_id}.json").write_text(
            record.model_dump_json(indent=2), encoding="utf-8"
        )
        trial_dir = job_dir / record.trial_dir
        gate = gate_result(
            trial_dir,
            CHECKPOINTS,
            gated=trial.arm == GATED_ARM,
            seed=_digest("gate", trial.trial_id),
            problem=PROBLEM,
        )
        store.put(
            assessments.assessment(
                CODE_QUALITY.ref,
                trial_dir,
                AssessResult(
                    status=AssessStatus.OK,
                    observations=tuple(trial.observations),
                    evidence=tuple(trial.evidence),
                ),
            )
        )
        store.put(
            assessments.assessment(
                CORRECTNESS.ref,
                trial_dir,
                AssessResult(status=AssessStatus.OK, observations=tuple(trial.correctness)),
            )
        )
        store.put(assessments.assessment(VERIFICATION_EFFORT.ref, trial_dir, gate))
        for decision in _decision_records(trial):
            audit.append(decision)
    return dest


SCORERS = (CORRECTNESS.ref, CODE_QUALITY.ref, VERIFICATION_EFFORT.ref)
JOBS_ROOT = Path("jobs")


@dataclass(frozen=True)
class SyntheticQuality:
    """The synthetic design as in-memory inputs: no run directory, store or audit log is written.

    ``assessments`` are keyed by ``(review id, scorer ref)``; ``gates`` by each step's agent dir.
    """

    trials: tuple[TrialSource, ...]
    assessments: dict[tuple[str, str], Assessment]
    decisions: tuple[DecisionRecord, ...]
    gates: dict[Path, StepGate]

    def find(self, ref: str, trial: TrialSource) -> Assessment | None:
        return self.assessments.get((trial.review_id, ref))

    def audit(self) -> JevAudit:
        by_trial: dict[str, list[DecisionRecord]] = {}
        for decision in self.decisions:
            by_trial.setdefault(str(decision.trial), []).append(decision)
        return JevAudit(decisions=by_trial, labels={})

    def read_gate(self, trace_dir: Path) -> StepGate | None:
        return self.gates.get(trace_dir)


def experiment_info(*, baseline_arm: str | None = ARMS[0]) -> ExperimentInfo:
    """What the synthetic lock says (the run's lock is not read from a file)."""
    return ExperimentInfo(
        lock_sha256=_digest("lock"),
        baseline_arm=baseline_arm,
        metric=default_metric(len(CHECKPOINTS)),
        analysis=synthetic_analysis(
            baseline_arm=baseline_arm or "", problem=PROBLEM, checkpoints=len(CHECKPOINTS)
        ),
        scorers=SCORERS,
    )


def memory_assessment(ref: str, trial: TrialSource, result: AssessResult) -> Assessment:
    """An Assessment of ``trial`` under a made-up key (steps carry the trial's snapshots)."""
    steps = [
        {"step": step, "snapshot": digest} for step, digest in trial.snapshot_digests().items()
    ]
    return Assessment(
        key=_digest("assessment", ref, trial.review_id),
        scorer=ScorerIdentity(
            ref=ref, manifest_digest=_digest("manifest", ref), implementation_digest=None
        ),
        config={},
        tool_lock={},
        inputs={"steps": steps},
        result=result,
    )


def memory_trial_source(trial: _Trial, experiment: ExperimentInfo) -> TrialSource:
    record = _record(trial)
    return TrialSource(
        jobs_root=JOBS_ROOT,
        job_dir=JOBS_ROOT / EXPERIMENT / RUN_ID,
        job_id=RUN_ID,
        finished_at=_TIMESTAMP,
        record=record,
        experiment=experiment,
    )


def synthetic_quality() -> SyntheticQuality:
    """The synthetic design's trials, Assessments, Jev decisions and gate records in memory."""
    experiment = experiment_info()
    sources: list[TrialSource] = []
    assessments: dict[tuple[str, str], Assessment] = {}
    decisions: list[DecisionRecord] = []
    gates: dict[Path, StepGate] = {}
    for trial in _designs():
        source = memory_trial_source(trial, experiment)
        sources.append(source)
        found: dict[str, StepGate] | None = None
        if trial.arm == GATED_ARM:
            records = gate_records(CHECKPOINTS, _digest("gate", trial.trial_id))
            found = {checkpoint: parse_gate(record) for checkpoint, record in records.items()}
            gates |= {
                source.trial_dir / "steps" / checkpoint / "agent": gate
                for checkpoint, gate in found.items()
            }
        results = {
            CODE_QUALITY.ref: AssessResult(
                status=AssessStatus.OK,
                observations=tuple(trial.observations),
                evidence=tuple(trial.evidence),
            ),
            CORRECTNESS.ref: AssessResult(
                status=AssessStatus.OK, observations=tuple(trial.correctness)
            ),
            VERIFICATION_EFFORT.ref: memory_gate_result(found, CHECKPOINTS),
        }
        for ref, result in results.items():
            assessments[(source.review_id, ref)] = memory_assessment(ref, source, result)
        decisions.extend(_decision_records(trial))
    return SyntheticQuality(tuple(sources), assessments, tuple(decisions), gates)
