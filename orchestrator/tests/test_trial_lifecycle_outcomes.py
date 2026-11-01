"""What a trial leaves behind: contained Raidar failures, finish review and the written record.

Memory inputs and injected runners only; files are an in-memory ``Path`` text store.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from harbor.models.trial.config import AgentConfig

from raidar.contract.job_layout import TRIAL_RECORDS_DIR, TRIALS_DIR
from raidar.contract.trial import (
    CheckpointState,
    ExecutionValidity,
    PhaseSample,
    PlannedTrial,
    ProgressionRule,
    TrialHashes,
    TrialRecord,
    ValidityCheck,
)
from raidar.engine.trial import finish, run
from raidar.engine.trial import record as record_module
from raidar.engine.trial.context import JobContext
from raidar.engine.trial.provision import TrialProvision
from raidar.engine.trial.record import (
    RUNNER_EXCEPTION,
    RunnerFailure,
    runner_failure_record,
    write_record,
)

CONTINUE = ProgressionRule(mode="continue", reward_key="reward")
HALT = ProgressionRule(mode="halt_on_failure", reward_key="reward", threshold=1.0)
CHECKPOINTS = ("s1", "s2", "s3")
JOB = Path("/virtual/job")


def _trial(steps: tuple[str, ...] = ("s1", "s2")) -> PlannedTrial:
    digest = "0" * 8
    return PlannedTrial(
        trial_id="t0",
        attempt=0,
        order=0,
        sequence=0,
        arm="base",
        problem="prob",
        problem_index=0,
        steps=steps,
        hashes=TrialHashes(
            lock=digest,
            problem=digest,
            arm=digest,
            workflow=digest,
            packages=digest,
            primitive_code=digest,
            agent=digest,
            protocol=digest,
            trial=digest,
        ),
    )


def _ctx(
    job_dir: Path,
    *,
    guard: Any = None,
    pinned: bool = True,
    override: str | None = None,
    runner: Any = None,
) -> JobContext:
    manifest = SimpleNamespace(
        problem="prob",
        steps=[SimpleNamespace(name=name, checkpoint=f"cp-{name}") for name in CHECKPOINTS],
        grader=SimpleNamespace(test_dependencies=()),
    )
    pins = SimpleNamespace(exclude_newer="2025-01-01")
    dependencies = SimpleNamespace(pins=pins, locks=SimpleNamespace()) if pinned else None
    bundle = SimpleNamespace(manifest=manifest, dependencies=dependencies, root=job_dir / "bundle")
    problem = SimpleNamespace(
        task_dir=job_dir / "problem-task",
        bundle=bundle,
        reference=lambda step: job_dir / "reference" / step,
    )
    plan = SimpleNamespace(
        experiment_id="exp",
        rule=CONTINUE,
        options=SimpleNamespace(max_checkpoints=2, agent_override=override),
        loaded=SimpleNamespace(),
        arms={"base": SimpleNamespace(agent_config=AgentConfig(name="oracle"))},
        problems={0: SimpleNamespace(steps=CHECKPOINTS)},
        pack=SimpleNamespace(python="3.12"),
    )
    target = SimpleNamespace(
        guard=guard, selection=SimpleNamespace(grader=SimpleNamespace(docker_context="grader-ctx"))
    )
    kwargs = {} if runner is None else {"runner": runner}
    return JobContext(
        plan=plan,  # type: ignore[arg-type]
        job_dir=job_dir,
        job_id="job",
        problems={0: problem},  # type: ignore[dict-item]
        target=target,  # type: ignore[arg-type]
        **kwargs,
    )


def _infra_record(job_dir: Path, *, rule: ProgressionRule = CONTINUE) -> TrialRecord:
    return runner_failure_record(
        RunnerFailure(
            _trial(),
            tuple((step, f"cp-{step}") for step in CHECKPOINTS),
            job_dir / TRIALS_DIR / "t0",
            ValueError("bad"),
        ),
        experiment="exp",
        job_id="job",
        job_dir=job_dir,
        rule=rule,
        max_checkpoints=2,
        agent_override=None,
    )


class _MemoryFiles:
    """``Path`` text writes and directory creation kept in memory, reads falling through."""

    def __init__(self) -> None:
        self.files: dict[Path, str] = {}
        self.dirs: set[Path] = set()


@pytest.fixture
def disk(monkeypatch: pytest.MonkeyPatch) -> _MemoryFiles:
    memory = _MemoryFiles()
    real_read = Path.read_text

    def mkdir(self: Path, *_: object, **__: object) -> None:
        memory.dirs.add(self)

    def write_text(self: Path, data: str, *_: object, **__: object) -> int:
        memory.files[self] = data
        return len(data)

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        return memory.files[self] if self in memory.files else real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(Path, "write_text", write_text)
    monkeypatch.setattr(Path, "read_text", read_text)
    return memory


def _no_audit(monkeypatch: pytest.MonkeyPatch, checks: tuple[ValidityCheck, ...] = ()) -> None:
    monkeypatch.setattr(finish, "audit_and_write", lambda *_: checks)


def _stored(disk: _MemoryFiles, trial_id: str = "t0") -> TrialRecord:
    path = JOB / TRIAL_RECORDS_DIR / f"{trial_id}.json"
    return TrialRecord.model_validate_json(disk.files[path])


def test_job_context_names_the_grader_docker_context_and_the_trials_directory() -> None:
    ctx = _ctx(JOB)
    assert ctx.docker_context == "grader-ctx"
    assert ctx.trials_dir == JOB / TRIALS_DIR


async def test_a_raidar_failure_is_recorded_as_an_invalid_infra_trial_not_lost(
    disk: _MemoryFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(run, "_materialise", refuse)
    _no_audit(monkeypatch)

    record = await run.run_trial(_ctx(JOB), _trial())

    assert not record.valid
    (check,) = record.validity.checks
    assert (check.name, check.passed) == (RUNNER_EXCEPTION, False)
    assert check.detail == "OSError: disk full"
    assert record.trial_exception == "OSError"
    attempted, also_attempted, truncated = record.step_results
    for result in (attempted, also_attempted):
        assert result.checkpoint_state is CheckpointState.INFRA_ERROR
        assert result.infra_reasons == (f"{RUNNER_EXCEPTION}:OSError",)
    assert truncated.checkpoint_state is CheckpointState.NOT_ATTEMPTED
    assert truncated.termination == "truncated"
    assert record.reached == (True, False, False)
    assert record.trial_dir == f"{TRIALS_DIR}/t0"
    assert "OSError: disk full" in disk.files[JOB / TRIALS_DIR / "t0" / run.RUNNER_ERROR_FILE]
    assert _stored(disk) == record


def _materialised(disk: _MemoryFiles) -> SimpleNamespace:
    task = JOB / "materialised"
    disk.files[task / "task.toml"] = ""
    return SimpleNamespace(path=task, arm_apply_s=0.0)


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        agent=SimpleNamespace(override_timeout_sec=600),
        verifier=SimpleNamespace(override_timeout_sec=300, kwargs={"grader_timeout_s": 120}),
    )


async def test_a_problem_without_a_dependency_pin_is_refused_before_the_trial_starts(
    disk: _MemoryFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[object] = []

    async def runner(*args: object) -> object:
        started.append(args)
        raise AssertionError("the trial must not start")

    monkeypatch.setattr(run, "materialise_task", lambda *_, **__: _materialised(disk))
    monkeypatch.setattr(run, "environment_cold", lambda _: False)
    monkeypatch.setattr(run, "harbor_trial_config", lambda *_, **__: _config())
    _no_audit(monkeypatch)

    record = await run.run_trial(_ctx(JOB, pinned=False, runner=runner), _trial())

    assert not started
    assert not record.valid
    assert "prob carries no dependency pin: re-import it" in record.validity.checks[0].detail
    assert record.trial_exception == "ValueError"


@pytest.mark.parametrize(
    ("override", "references"),
    [(None, None), ("oracle", {"s1": JOB / "reference" / "s1", "s2": JOB / "reference" / "s2"})],
)
async def test_a_runner_exception_is_kept_on_the_classified_trial_with_its_limits(
    disk: _MemoryFiles,
    monkeypatch: pytest.MonkeyPatch,
    override: str | None,
    references: dict[str, Path] | None,
) -> None:
    seen: dict[str, Any] = {}

    def materialise(*_: object, **kwargs: Any) -> SimpleNamespace:
        seen.update(kwargs)
        return _materialised(disk)

    async def runner(config: object, bundle_root: Path, provisions: tuple[Any, ...]) -> object:
        seen["provisions"] = tuple(provision.slug for provision in provisions)
        seen["bundle_root"] = bundle_root
        return SimpleNamespace(trial_dir=JOB / TRIALS_DIR / "t0", exception_type="Timeout")

    monkeypatch.setattr(run, "materialise_task", materialise)
    monkeypatch.setattr(run, "environment_cold", lambda _: False)
    monkeypatch.setattr(run, "environment_image_id", lambda _: "sha256:image")
    monkeypatch.setattr(run, "harbor_trial_config", lambda *_, **__: _config())
    monkeypatch.setattr(run, "seed_locks", lambda *_: ())
    monkeypatch.setattr(
        run, "workspace_seed", lambda *_: TrialProvision("seed", {}, "install", "check")
    )
    _no_audit(monkeypatch)

    record = await run.run_trial(_ctx(JOB, override=override, runner=runner), _trial())

    assert seen["references"] == references
    assert seen["provisions"] == ("seed",)
    assert seen["bundle_root"] == JOB / "bundle"
    assert record.trial_exception == "Timeout"
    assert record.agent_override == override
    assert all(r.checkpoint_state is not CheckpointState.SOLVED for r in record.step_results)
    assert record.parity is not None
    assert record.parity.image_id == "sha256:image"
    limits = record.parity.limits
    assert (limits["agent_timeout_s"], limits["verifier_timeout_s"]) == (600, 300)
    assert limits["grader_timeout_s"] == 120
    assert _stored(disk) == record


@pytest.mark.parametrize(("rule", "source"), [(CONTINUE, "derived"), (HALT, "executed")])
def test_a_failed_trial_reports_reached_by_how_its_progression_rule_ran(
    rule: ProgressionRule, source: str
) -> None:
    record = _infra_record(JOB, rule=rule)
    assert record.reached_source == source
    assert record.progression == rule
    assert record.reached == (True, False, False)


def test_a_failed_trials_detail_is_bounded_and_its_outside_directory_stays_absolute() -> None:
    record = runner_failure_record(
        RunnerFailure(
            _trial(), (("s1", "cp-s1"),), Path("/elsewhere/t0"), RuntimeError("x" * 2000)
        ),
        experiment="exp",
        job_id="job",
        job_dir=JOB,
        rule=CONTINUE,
        max_checkpoints=None,
        agent_override=None,
    )
    assert record.trial_dir == "/elsewhere/t0"
    detail = record.validity.checks[0].detail
    assert len(detail) == 500
    assert detail.startswith("RuntimeError: xxx")


def test_writing_a_record_appends_the_measured_write_time_after_its_existing_phases(
    disk: _MemoryFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = iter([10.0, 10.25])
    monkeypatch.setattr(record_module, "time", SimpleNamespace(perf_counter=lambda: next(ticks)))
    earlier = PhaseSample(phase="environment_start", seconds=3.0)
    base = _infra_record(JOB).model_copy(update={"phases": (earlier,)})
    path = JOB / "nested" / "t0.json"

    final = write_record(path, base)

    assert final.phases == (earlier, PhaseSample(phase="raidar_result_write", seconds=0.25))
    assert TrialRecord.model_validate_json(disk.files[path]) == final


class _Guard:
    """A target guard that found the Docker target lost and marks the trial invalid."""

    def __init__(self) -> None:
        self.reviewed: list[tuple[Path, Path]] = []

    async def review(
        self, record: TrialRecord, *, trial_dir: Path, trials_dir: Path
    ) -> TrialRecord:
        self.reviewed.append((trial_dir, trials_dir))
        lost = ValidityCheck(name="target_reachable", passed=False, detail="gone")
        return record.model_copy(
            update={"validity": ExecutionValidity(checks=(*record.validity.checks, lost))}
        )


async def test_finishing_keeps_the_guards_verdict_then_the_audits_in_the_written_record(
    disk: _MemoryFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = ValidityCheck(name="health_budgets", passed=False, detail="over budget")
    _no_audit(monkeypatch, (audit,))
    guard = _Guard()

    written = await finish.finish_trial(_ctx(JOB, guard=guard), _trial(), _infra_record(JOB))

    assert guard.reviewed == [(JOB / TRIALS_DIR / "t0", JOB / TRIALS_DIR)]
    assert [check.name for check in written.validity.checks] == [
        RUNNER_EXCEPTION,
        "target_reachable",
        "health_budgets",
    ]
    assert not written.valid
    assert _stored(disk) == written
    assert JOB / TRIALS_DIR / "t0" in disk.dirs


async def test_a_passing_audit_leaves_the_trial_validity_to_its_own_checks(
    disk: _MemoryFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = ValidityCheck(name="health_budgets", passed=True)
    _no_audit(monkeypatch, (clean,))
    passing = _infra_record(JOB).model_copy(update={"validity": ExecutionValidity(checks=())})

    written = await finish.finish_trial(_ctx(JOB), _trial(), passing)

    assert written.valid
    assert written.validity.checks == (clean,)
