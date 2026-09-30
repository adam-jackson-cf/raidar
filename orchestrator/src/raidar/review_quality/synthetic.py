"""Synthetic new-model fixture for the Quality element (review-surface development and tests).

Writes, under one directory, clearly labelled synthetic data in the real
WS-D/WS-F/WS-H shapes:

- ``experiment.yaml``, a digest-checked ``experiment.lock.json`` beside it
  (baseline arm and headline policy, as a real lock carries them), and
  ``runs/synthetic-quality/<run>/`` run directories (``run.json`` naming the
  lock digest + ``trajectories/*.json`` TrajectoryRecords);
- ``store/``: ``code-quality@1`` and ``correctness@1`` Assessments whose
  inputs carry each trial's snapshot digests (correctness in the real
  scorer's shape: per-checkpoint ``solved`` and ``strict_pass_rate``, and
  trajectory ``solved_checkpoints`` and ``reached``), plus a Jev audit of
  rubric decisions for the label-export flow.

The experiment id, run ids, problem and every snapshot digest carry a
``synthetic`` marker, and the projection built from it sets
``synthetic: true``. It is never benchmark evidence.

Built-in shape (deterministic, seeded): two arms (``baseline``, ``guided``)
x 6 blocks x one 5-checkpoint problem. ``guided`` is better on E and R;
block 4 of ``guided`` is an invalid trajectory (infra-excluded); block 2 of
``baseline`` has an ineligible H at checkpoint 3, so its headline S is
ineligible with reasons.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from raidar.execution.models import (
    CheckpointAttempt,
    CheckpointState,
    ExecutionValidity,
    PlannedTrial,
    ProgressionRule,
    SnapshotInfo,
    TrajectoryRecord,
    TrialHashes,
    ValidityCheck,
)
from raidar.jev.audit import (
    AuditLog,
    DecisionRecord,
    DecisionStage,
    InputSufficiency,
    SufficiencyLevel,
)
from raidar.jev.policies import PolicyMode
from raidar.jev.rubric import RUBRIC_POLICY
from raidar.review_quality.synthetic_lock import default_headline_policy, write_synthetic_lock
from raidar.scorers.builtin_manifests import CODE_QUALITY, CORRECTNESS
from raidar.scorers.sdk.manifest import ScorerManifest
from raidar.scorers.sdk.result import (
    AssessResult,
    AssessStatus,
    Evidence,
    Observation,
    Span,
    transition_subject,
)
from raidar.scorers.store import Assessment, AssessmentStore, ScorerIdentity, assessment_key

EXPERIMENT = "synthetic-quality"
PROBLEM = "synthetic/ledger@0"
RUN_ID = "synthetic-run-1"
ARMS = ("baseline", "guided")
BLOCKS = 6
CHECKPOINTS = tuple(f"checkpoint_{index}" for index in range(1, 6))
INVALID = ("guided", 4)
INELIGIBLE_H = ("baseline", 2, "checkpoint_3")
_SEED = 20260930
_TIMESTAMP = "2026-09-30T00:00:00.000Z"
_FILES = ("ledger/core.py", "ledger/report.py", "ledger/cli.py")


def _digest(*parts: object) -> str:
    return hashlib.sha256("synthetic|".join(map(str, parts)).encode()).hexdigest()


@dataclass
class _Trial:
    arm: str
    block: int
    rng: random.Random
    observations: list[Observation] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    correctness: list[Observation] = field(default_factory=list)

    @property
    def trial_id(self) -> str:
        return f"b{self.block:02d}-p0-{self.arm}"

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
    if (trial.arm, trial.block, checkpoint) == INELIGIBLE_H:
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


def _inputs(trial: _Trial) -> dict[str, object]:
    return {
        "inventory_policy": {"generated_globs": [], "test_globs": []},
        "problem": PROBLEM,
        "starter": None,
        "steps": [
            {
                "checkpoint": checkpoint,
                "oracle_record": _digest("record", trial.trial_id, checkpoint),
                "reached": True,
                "snapshot": trial.snapshot(checkpoint),
                "step": checkpoint,
                "trace": _digest("trace", trial.trial_id, checkpoint),
            }
            for checkpoint in CHECKPOINTS
        ],
    }


def _assessment(manifest: ScorerManifest, trial: _Trial, result: AssessResult) -> Assessment:
    identity = ScorerIdentity(
        ref=manifest.ref, manifest_digest=manifest.digest(), implementation_digest=None
    )
    inputs = _inputs(trial)
    key = assessment_key(identity, config={}, tool_lock={"synthetic": True}, inputs=inputs)
    return Assessment(
        key=key,
        scorer=identity,
        config={},
        tool_lock={"synthetic": True},
        inputs=inputs,
        result=result,
    )


def _record(trial: _Trial) -> TrajectoryRecord:
    valid = (trial.arm, trial.block) != INVALID
    attempts = tuple(
        CheckpointAttempt(
            trial_id=trial.trial_id,
            arm=trial.arm,
            block=trial.block,
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
        for name in ("lock", "problem", "arm", "condition", "agent", "protocol", "trial")
    }
    return TrajectoryRecord(
        experiment=EXPERIMENT,
        run_id=RUN_ID,
        trial=PlannedTrial(
            trial_id=trial.trial_id,
            block=trial.block,
            order=ARMS.index(trial.arm),
            sequence=trial.block * len(ARMS) + ARMS.index(trial.arm),
            arm=trial.arm,
            problem=PROBLEM,
            problem_index=0,
            steps=CHECKPOINTS,
            hashes=TrialHashes(**hashes),
        ),
        trial_dir=f"trials/{trial.trial_id}",
        progression=ProgressionRule(mode="continue", reward_key="strict_pass_rate"),
        attempts=attempts,
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


def _decisions(audit: AuditLog, trial: _Trial) -> None:
    if trial.block > 1:
        return
    for number, checkpoint in enumerate(CHECKPOINTS[:3]):
        flagged = (number + trial.block) % 2 == 0
        audit.append(
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


def generate_quality_fixture(dest: Path) -> Path:
    """Write the synthetic runs, experiment file and store under ``dest`` (replaced)."""
    shutil.rmtree(dest, ignore_errors=True)
    run_dir = dest / "runs" / EXPERIMENT / RUN_ID
    (run_dir / "trajectories").mkdir(parents=True)
    experiment_file = dest / "experiment.yaml"
    experiment_file.write_text(
        f"# synthetic fixture: not benchmark evidence\nid: {EXPERIMENT}\n"
        "analysis:\n  baseline_arm: baseline\n  blocks: 6\n  seed: 20260930\n"
        "assessment:\n  scorers: [correctness@1, code-quality@1]\n",
        encoding="utf-8",
    )
    lock_sha256 = write_synthetic_lock(
        experiment_file,
        experiment=EXPERIMENT,
        baseline_arm=ARMS[0],
        headline_policy=default_headline_policy(len(CHECKPOINTS)),
    )
    run_dir.joinpath("run.json").write_text(
        json.dumps(
            {
                "kind": "raidar-run",
                "synthetic": True,
                "experiment": EXPERIMENT,
                "experiment_file": str(experiment_file),
                "lock_sha256": lock_sha256,
                "run_id": RUN_ID,
                "finished_at": _TIMESTAMP,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    store = AssessmentStore(dest / "store")
    audit = AuditLog.for_store(dest / "store")
    rng = random.Random(_SEED)
    for block in range(BLOCKS):
        for arm in ARMS:
            trial = _Trial(arm=arm, block=block, rng=random.Random(rng.random()))
            _measure(trial)
            store.put(
                _assessment(
                    CODE_QUALITY,
                    trial,
                    AssessResult(
                        status=AssessStatus.OK,
                        observations=tuple(trial.observations),
                        evidence=tuple(trial.evidence),
                    ),
                )
            )
            store.put(
                _assessment(
                    CORRECTNESS,
                    trial,
                    AssessResult(status=AssessStatus.OK, observations=tuple(trial.correctness)),
                )
            )
            record = _record(trial)
            (run_dir / "trajectories" / f"{trial.trial_id}.json").write_text(
                record.model_dump_json(indent=2), encoding="utf-8"
            )
            _decisions(audit, trial)
    return dest


def main() -> None:
    target = generate_quality_fixture(Path(sys.argv[1]))
    print(f"Wrote synthetic Quality fixture to {target}")


if __name__ == "__main__":
    main()
