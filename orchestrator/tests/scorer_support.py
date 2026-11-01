"""Builders for Scorer SDK and Assessment store tests: stored trials and fake scorers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from raidar.contract.snapshot import tree_digest
from raidar.grading.bundle import (
    BundleManifest,
    GraderSpec,
    VerifierBundle,
    VerifierStepSpec,
    write_bundle,
)
from raidar.grading.models import CaseGroup, CaseResult, CaseStatus, VerifierGrade
from raidar.grading.records import (
    BundleRef,
    CoverageRef,
    GraderRef,
    SnapshotRef,
    StepRecord,
    step_record_dir,
    stored_snapshot_path,
    write_step_record,
)
from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.inventory import FileRole
from raidar.scoring.framework.manifest import (
    Direction,
    EvidenceInput,
    Exactness,
    MetricLevel,
    MetricSpec,
    MetricType,
    ScorerManifest,
    Tier,
)
from raidar.scoring.framework.registry import ScorerRegistration, ScorerRegistry
from raidar.scoring.framework.rescore import AssessmentRecord, assess
from raidar.scoring.framework.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Evidence,
    Exclusion,
    Observation,
    Span,
)
from raidar.scoring.framework.store import AssessmentStore, ScorerIdentity
from raidar.scoring.framework.trial_inputs import (
    agent_execution_seconds,
    blind_agent_result,
    ran_judge,
)

APP_V1 = {"app/main.py": "def add(a, b):\n    return a + b\n"}
APP_V2 = {
    "app/main.py": "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n",
    "tests/test_main.py": "def test_add():\n    assert True\n",
}


def _grade(step: str, statuses: Sequence[CaseStatus]) -> VerifierGrade:
    cases = tuple(
        CaseResult(
            id=f"test_{step}.py::case_{index}",
            group=CaseGroup.CORE,
            checkpoint_origin=step,
            status=status,
        )
        for index, status in enumerate(statuses)
    )
    return VerifierGrade(step=step, checkpoint=step, cases=cases)


def write_files(root: Path, files: Mapping[str, str | bytes]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)


@dataclass(frozen=True)
class StepFixture:
    name: str
    files: Mapping[str, str | bytes]
    statuses: tuple[CaseStatus, ...] | None  # None: the verifier never graded this step
    usage: dict[str, int] | None = None


@dataclass(frozen=True)
class MemoryStep:
    """A checkpoint of an in-memory trajectory: its oracle grade, or None when never graded."""

    name: str
    grade: VerifierGrade | None
    rewards: Mapping[str, float] | None = None  # None: the grade's own rewards


def memory_step(name: str, statuses: Sequence[CaseStatus] | None) -> MemoryStep:
    """A checkpoint whose grade has one core case per status (None: not graded)."""
    return MemoryStep(name, None if statuses is None else _grade(name, statuses))


def memory_record(
    name: str, grade: VerifierGrade, *, rewards: Mapping[str, float] | None = None, problem="toy"
) -> StepRecord:
    """An oracle step record built in memory (nothing is stored)."""
    return StepRecord(
        step=name,
        checkpoint=name,
        bundle=BundleRef(root="bundle", digest="b" * 64, problem=problem),
        snapshot=SnapshotRef(
            requested="artifact",
            used="artifact",
            digest="s" * 64,
            stored_path=stored_snapshot_path(name),
        ),
        grader=GraderRef(
            image="grader:x", environment_id="sha256:e", image_built=False, timeout_s=60
        ),
        grade=grade,
        summary=grade.summary(),
        coverage=CoverageRef(enabled=False),
        rewards=dict(rewards) if rewards is not None else grade.rewards(),
    )


def memory_context(
    steps: Sequence[MemoryStep],
    *,
    planned: Sequence[str] = (),
    config: Mapping[str, object] | None = None,
    problem: str = "toy",
) -> AssessContext:
    """A trajectory's scorer inputs without a stored trial: records only, no snapshot or trace.

    ``planned`` adds checkpoints the trial never reached.
    """
    inputs = []
    for step in steps:
        record = (
            None
            if step.grade is None
            else memory_record(step.name, step.grade, rewards=step.rewards, problem=problem)
        )
        inputs.append(
            StepInputs(
                step=step.name,
                reached=True,
                checkpoint=step.name,
                snapshot=None,
                snapshot_digest=None,
                record=record,
                record_digest=(
                    None
                    if record is None
                    else hashlib.sha256(record.model_dump_json().encode()).hexdigest()
                ),
                trace_dir=None,
                trace_digest=None,
                agent_result=None,
            )
        )
    inputs += [
        StepInputs(
            step=name,
            reached=False,
            checkpoint=name,
            snapshot=None,
            snapshot_digest=None,
            record=None,
            record_digest=None,
            trace_dir=None,
            trace_digest=None,
            agent_result=None,
        )
        for name in planned
    ]
    return AssessContext(problem=problem, steps=tuple(inputs), config=dict(config or {}))


def fixed_identity(registration: ScorerRegistration) -> ScorerIdentity:
    """A scorer identity whose implementation digest names the implementation, not its source.

    Store tests exercise keys and outcomes, not the source closure ``scorer_identity`` hashes.
    """
    return ScorerIdentity.model_validate(
        {
            "ref": registration.manifest.ref,
            "manifest_digest": registration.manifest.digest(),
            "implementation_digest": hashlib.sha256(
                str(registration.implementation).encode()
            ).hexdigest(),
        }
    )


def assess_fixed(
    store: AssessmentStore,
    registration: ScorerRegistration,
    ctx: AssessContext,
    *,
    trial: str = "trial",
    replay: bool = False,
) -> AssessmentRecord:
    """``rescore.assess`` under ``fixed_identity``."""
    identity = fixed_identity(registration)
    return assess(store, registration, ctx, trial=trial, replay=replay, identity=identity)


def traced_memory_context(
    trace_root: Path,
    steps: Sequence[Any],
    *,
    planned: Sequence[str] = (),
    config: Mapping[str, object] | None = None,
    problem: str = "toy",
) -> AssessContext:
    """Scorer inputs for steps carrying Harbor usage, timing and a trajectory, without a trial.

    Each of ``steps`` has ``name``, ``trace`` (the ATIF document or None), ``usage``,
    ``execution`` and ``statuses`` (see ``trace_trial_support.TracedStep``). Only a
    trajectory file is written, under ``trace_root``, for scorers that read it.
    """
    base = memory_context(
        [memory_step(step.name, step.statuses) for step in steps], planned=planned, config=config
    )
    inputs = []
    for step, memory in zip(steps, base.steps, strict=False):
        trace_dir = None
        if step.trace is not None:
            trace_dir = trace_root / step.name
            trace_dir.mkdir(parents=True, exist_ok=True)
            (trace_dir / "trajectory.json").write_text(json.dumps(step.trace))
        inputs.append(
            replace(
                memory,
                trace_dir=trace_dir,
                trace_digest=hashlib.sha256(
                    json.dumps([step.trace, step.usage, step.execution], sort_keys=True).encode()
                ).hexdigest(),
                agent_result=blind_agent_result(step.usage),
                agent_execution_s=agent_execution_seconds(step.execution),
                judged=ran_judge(step.usage),
            )
        )
    return replace(base, steps=(*inputs, *base.steps[len(inputs) :]))


def snapshot_context(
    root: Path,
    steps: Sequence[StepFixture],
    *,
    planned: Sequence[str] = (),
    config: Mapping[str, object] | None = None,
) -> AssessContext:
    """Scorer inputs whose steps carry graded records and a snapshot tree, without a trial.

    Only each step's snapshot files are written, under ``root``.
    """
    base = memory_context(
        [memory_step(step.name, step.statuses) for step in steps], planned=planned, config=config
    )
    inputs = []
    for fixture, memory in zip(steps, base.steps, strict=False):
        snapshot = root / fixture.name / "snapshot"
        write_files(snapshot, fixture.files)
        trace = root / fixture.name / "agent"
        write_files(trace, {"trajectory.json": "{}\n"})
        inputs.append(
            replace(
                memory,
                snapshot=snapshot,
                snapshot_digest=tree_digest(snapshot),
                trace_dir=trace,
                trace_digest=f"trace-{fixture.name}",
            )
        )
    return replace(base, steps=(*inputs, *base.steps[len(inputs) :]))


def two_step_context(root: Path) -> AssessContext:
    """The in-memory counterpart of ``two_step_trial``."""
    return snapshot_context(
        root,
        [
            StepFixture("checkpoint_1", APP_V1, (CaseStatus.PASSED, CaseStatus.PASSED)),
            StepFixture("checkpoint_2", APP_V2, (CaseStatus.PASSED, CaseStatus.FAILED)),
        ],
    )


def write_toy_bundle(
    root: Path, steps: Sequence[str], *, entry_file: str | None = None, problem: str = "toy"
) -> VerifierBundle:
    """A host-side verifier bundle planning ``steps`` for problem ``toy``."""
    tests = root.parent / f"{root.name}-tests"
    write_files(tests, {f"test_{name}.py": "def test_ok():\n    pass\n" for name in steps})
    manifest = BundleManifest(
        problem=problem,
        grader=GraderSpec(base_image="local"),
        steps=tuple(
            VerifierStepSpec(
                name=name,
                checkpoint=name,
                test_files=(f"test_{name}.py",),
                pytest_args=(f"{{tests}}/test_{name}.py",),
            )
            for name in steps
        ),
        entry_file=entry_file,
    )
    return write_bundle(root, manifest, tests)


def write_trial(
    trial_dir: Path,
    steps: Sequence[StepFixture],
    *,
    planned: Sequence[str] = (),
    entry_file: str | None = None,
    problem: str = "toy",
) -> Path:
    """A stored multi-step Harbor trial with host-side verifier records.

    ``planned`` adds checkpoints the bundle plans after the executed ``steps``
    (a trial that halted or errored before reaching them).
    """
    bundle = write_toy_bundle(
        trial_dir.parent / f"{trial_dir.name}-bundle",
        [*(s.name for s in steps), *planned],
        entry_file=entry_file,
        problem=problem,
    )
    step_results = []
    for step in steps:
        snapshot = trial_dir / stored_snapshot_path(step.name)
        write_files(snapshot, step.files)
        (trial_dir / "steps" / step.name / "agent").mkdir(parents=True, exist_ok=True)
        (trial_dir / "steps" / step.name / "agent" / "trajectory.json").write_text("{}\n")
        step_results.append(
            {
                "step_name": step.name,
                "agent_result": {
                    "n_input_tokens": (step.usage or {}).get("input", 0),
                    # Labels a scorer must never see (labels_blinded).
                    "model_usage": {"arm-model-label": {"n_input_tokens": 7}},
                    "metadata": {"model": "arm-model-label"},
                },
                # Harbor's aggregated trial reward must never feed a score.
                "verifier_result": {"rewards": {"solved": 0.0}},
            }
        )
        if step.statuses is None:
            continue
        grade = _grade(step.name, step.statuses)
        record = StepRecord(
            step=step.name,
            checkpoint=step.name,
            bundle=BundleRef(root=str(bundle.root), digest=bundle.digest(), problem=problem),
            snapshot=SnapshotRef(
                requested="artifact",
                used="artifact",
                digest=tree_digest(snapshot),
                stored_path=stored_snapshot_path(step.name),
            ),
            grader=GraderRef(
                image="grader:x", environment_id="sha256:e", image_built=False, timeout_s=60
            ),
            grade=grade,
            summary=grade.summary(),
            coverage=CoverageRef(enabled=False),
            rewards=grade.rewards(),
        )
        write_step_record(step_record_dir(trial_dir, step.name), record)
    result = {
        "task_name": "toy",
        "step_results": step_results,
        "verifier_result": {"rewards": {"solved": 0.0}},
    }
    task_dir = trial_dir.parent / f"{trial_dir.name}-task"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_dir.joinpath("task.toml").write_text(
        "\n".join(f'[[steps]]\nname = "{name}"' for name in [*(s.name for s in steps), *planned])
    )
    trial_dir.joinpath("config.json").write_text(json.dumps({"task": {"path": str(task_dir)}}))
    (trial_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return trial_dir


def two_step_trial(trial_dir: Path) -> Path:
    return write_trial(
        trial_dir,
        [
            StepFixture("checkpoint_1", APP_V1, (CaseStatus.PASSED, CaseStatus.PASSED)),
            StepFixture("checkpoint_2", APP_V2, (CaseStatus.PASSED, CaseStatus.FAILED)),
        ],
    )


def tree_state(root: Path) -> dict[str, tuple[str, int]]:
    """Content hash and mtime of every file under ``root`` (proves nothing was written)."""
    return {
        path.relative_to(root).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _metric(
    metric_id: str,
    level: MetricLevel,
    metric_type: MetricType,
    inputs: tuple[EvidenceInput, ...] = (),
) -> MetricSpec:
    return MetricSpec(
        inputs=inputs,
        id=metric_id,
        description=f"fake {metric_id}",
        level=level,
        type=metric_type,
        direction=Direction.HIGHER_BETTER,
        tier=Tier.DETERMINISTIC,
        exactness=Exactness.NATIVE,
    )


def fake_manifest(version: str = "1", *, extra_metric: bool = False) -> ScorerManifest:
    metrics = [
        _metric("production_files", MetricLevel.CHECKPOINT, MetricType.COUNT),
        _metric(
            "solved", MetricLevel.CHECKPOINT, MetricType.BOOLEAN, (EvidenceInput.ORACLE_CASES,)
        ),
        _metric("growth", MetricLevel.TRANSITION, MetricType.REAL),
        _metric(
            "solved_checkpoints",
            MetricLevel.TRAJECTORY,
            MetricType.COUNT,
            (EvidenceInput.ORACLE_CASES,),
        ),
    ]
    if extra_metric:
        metrics.append(_metric("test_files", MetricLevel.CHECKPOINT, MetricType.COUNT))
    return ScorerManifest(
        id="fake-quality",
        version=version,
        description="Test scorer over inventories and verifier records.",
        runtimes=("*",),
        evidence_required=(EvidenceInput.SNAPSHOT_CURRENT, EvidenceInput.ORACLE_CASES),
        metrics=tuple(metrics),
    )


def _unmeasured(ctx: AssessContext, name: str) -> tuple[AssessStatus, str] | None:
    """Why a step's snapshot cannot be measured, or None when it can."""
    inputs = ctx.step(name)
    if not inputs.reached:
        return AssessStatus.NOT_APPLICABLE, "checkpoint not reached"
    inventory = ctx.inventory(name)
    if inventory is None:
        return AssessStatus.UNAVAILABLE, "no snapshot for this step"
    if not inventory.eligible:
        return AssessStatus.INELIGIBLE, "; ".join(i.detail for i in inventory.invalid)
    return None


def _checkpoint_observations(ctx: AssessContext, name: str, extra: bool) -> list[Observation]:
    inventory = ctx.inventory(name)
    inputs = ctx.step(name)
    blocked = _unmeasured(ctx, name)
    counts = {"production_files": FileRole.PRODUCTION}
    if extra:
        counts["test_files"] = FileRole.TEST
    observations = [
        Observation(metric=metric, subject=name, status=blocked[0], reason=blocked[1])
        if blocked is not None
        else Observation(
            metric=metric,
            subject=name,
            status=AssessStatus.OK,
            value=len(inventory.with_role(role)) if inventory is not None else None,
        )
        for metric, role in counts.items()
    ]
    if inputs.record is None:
        observations.append(
            Observation(
                metric="solved",
                subject=name,
                status=AssessStatus.UNAVAILABLE,
                reason="step not graded" if inputs.reached else "checkpoint not reached",
            )
        )
    else:
        observations.append(
            Observation(
                metric="solved",
                subject=name,
                status=AssessStatus.OK,
                value=1.0 if inputs.record.grade.solved else 0.0,
            )
        )
    return observations


def _growth(ctx: AssessContext, prior: str, current: str) -> Observation:
    subject = f"{prior}->{current}"
    for step in (prior, current):
        blocked = _unmeasured(ctx, step)
        if blocked is not None:
            return Observation(
                metric="growth", subject=subject, status=blocked[0], reason=f"{step}: {blocked[1]}"
            )
    sizes = [len(inventory.files) for s in (prior, current) if (inventory := ctx.inventory(s))]
    return Observation(
        metric="growth", subject=subject, status=AssessStatus.OK, value=float(sizes[1] - sizes[0])
    )


class FakeScorer:
    """Deterministic scorer used to exercise the SDK, store and CLI end to end."""

    def __init__(self, manifest: ScorerManifest | None = None) -> None:
        self._manifest = manifest or fake_manifest()

    @property
    def manifest(self) -> ScorerManifest:
        return self._manifest

    def assess(self, ctx: AssessContext) -> AssessResult:
        extra = self._manifest.metric("test_files") is not None
        observations: list[Observation] = []
        evidence: list[Evidence] = []
        exclusions: list[Exclusion] = []
        for item in ctx.steps:
            observations += _checkpoint_observations(ctx, item.step, extra)
            inventory = ctx.inventory(item.step)
            measured = inventory if _unmeasured(ctx, item.step) is None else None
            for file in measured.with_role(FileRole.PRODUCTION) if measured else ():
                assert file.sha256 is not None
                evidence.append(
                    Evidence(
                        metric="production_files",
                        subject=item.step,
                        path=file.path,
                        sha256=file.sha256,
                        kind="production-file",
                        spans=(Span(line=1),),
                    )
                )
            exclusions += [
                Exclusion(subject=item.step, path=entry.path, reason=entry.reason)
                for entry in (inventory.exclusions if inventory else ())
            ]
        observations += [
            _growth(ctx, prior.step, current.step)
            for prior, current in zip(ctx.steps, ctx.steps[1:], strict=False)
        ]
        solved = sum(1 for s in ctx.steps if s.record is not None and s.record.grade.solved)
        observations.append(
            Observation(
                metric="solved_checkpoints",
                subject=TRAJECTORY_SUBJECT,
                status=AssessStatus.OK,
                value=solved,
            )
        )
        # Emit in reverse to prove the store's bytes do not depend on emission order.
        return AssessResult(
            status=AssessStatus.OK,
            observations=tuple(reversed(observations)),
            evidence=tuple(reversed(evidence)),
            exclusions=tuple(exclusions),
        )


def fake_scorer_v1() -> FakeScorer:
    return FakeScorer(fake_manifest("1"))


def fake_scorer_v2() -> FakeScorer:
    return FakeScorer(fake_manifest("2", extra_metric=True))


def fake_registry() -> ScorerRegistry:
    return ScorerRegistry(
        [
            ScorerRegistration(fake_manifest("1"), "tests.scorer_support:fake_scorer_v1"),
            ScorerRegistration(
                fake_manifest("2", extra_metric=True), "tests.scorer_support:fake_scorer_v2"
            ),
        ]
    )
