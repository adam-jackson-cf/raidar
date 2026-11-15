"""Job completion keeps a truthful manifest even when admission or a trial fails.

Every ``job.json``, finished or aborted, names the Raidar commit and dirty flag the job ran on,
so results that predate a code change are knowable.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raidar.contract.job_layout import DETACH_DIR, JOB_LOCK_FILE, JOB_MANIFEST
from raidar.contract.trial import CheckpointState, ProgressionRule
from raidar.design.experiment.loader import CliOverrides, LoadedExperiment
from raidar.design.experiment.schema import ExperimentSpec
from raidar.design.problem.models import ProblemId, ProblemManifest
from raidar.design.problem.store import ImportedProblem, ProblemStore
from raidar.design.problem.validation import ValidationRecord, Verdict
from raidar.engine.job import manifest, provenance, run
from raidar.engine.target.config import Selection, Target
from raidar.engine.target.guard import TargetGuard
from raidar.engine.target.job import JobTarget
from raidar.engine.trial.context import JobContext
from raidar.engine.trial.plan import ExperimentPlan, PlanOptions, ProblemTarget
from raidar.grading.bundle import BundleManifest, VerifierBundle
from raidar.subject.environment.python.pack_definition import PackSpec


def _record(trial: Any, states: tuple[CheckpointState, ...], valid: bool) -> Any:
    return SimpleNamespace(
        trial=trial,
        step_results=tuple(SimpleNamespace(checkpoint_state=state) for state in states),
        valid=valid,
    )


def _memory_job(monkeypatch: pytest.MonkeyPatch, ending: str) -> tuple[Any, ...]:
    clock = SimpleNamespace(now=lambda _zone: datetime(2026, 1, 1, tzinfo=UTC))
    monkeypatch.setattr(manifest, "datetime", clock)
    monkeypatch.setattr(run, "datetime", clock)
    trials = [SimpleNamespace(trial_id=name) for name in ("z", "a", "m", "unselected")]
    selected = [] if ending == "empty" else trials[:3]
    lock = {} if ending == "empty" else {"lock_sha256": "locked-input"}
    plan = SimpleNamespace(
        loaded=SimpleNamespace(
            path=Path("/experiment.yaml"),
            spec=SimpleNamespace(assessment=None, protocol=None),
        ),
        experiment_id="comparison",
        concurrency=4,
        trials=trials,
        selected={trial.trial_id for trial in selected},
        selected_trials=lambda: selected,
        lock=lock,
        lock_sha256="locked-input",
        to_json=lambda: {"selected": [trial.trial_id for trial in selected]},
    )
    files: dict[Path, dict[str, Any]] = {}

    def persist(path: Path, text: str, **_kwargs: Any) -> int:
        files[path] = json.loads(text)
        return len(text)

    monkeypatch.setattr(Path, "write_text", persist)
    monkeypatch.setattr(manifest, "write_assessment_config", lambda *_args: None)
    monkeypatch.setattr(
        provenance,
        "git_state",
        lambda _cwd, **_kwargs: {"commit": "c0ffee", "dirty": False, "error": None},
    )
    monkeypatch.setattr(provenance, "spec_source_digest", lambda: "spec")
    monkeypatch.setattr(provenance, "execution_source_digest", lambda: "execution")
    monkeypatch.setattr(manifest, "recipe_digests", lambda *_args: {})
    monkeypatch.setattr(manifest, "effective_limits", lambda *_args: {})
    monkeypatch.setattr(run, "create_job_dir", lambda _path: None)
    monkeypatch.setattr(run, "seal_trial", lambda trial_dir, *_args: f"sealed-{trial_dir.name}")

    async def inline(function: Any, *args: Any) -> Any:
        return function(*args)

    monkeypatch.setattr(asyncio, "to_thread", inline)
    started: list[str] = []
    states = (
        (CheckpointState.SOLVED, CheckpointState.EXCLUDED),
        (CheckpointState.FAILED, CheckpointState.INFRA_ERROR),
        (),
    )
    records = [
        _record(trial, state, index != 1)
        for index, (trial, state) in enumerate(zip(trials, states, strict=False))
    ]

    async def run_trial(_ctx: Any, trial: Any) -> Any:
        started.append(trial.trial_id)
        if ending == "trial_failure" and trial.trial_id == "a":
            raise RuntimeError("runner unavailable")
        return records[trials.index(trial)]

    monkeypatch.setattr(run, "run_trial", run_trial)

    class LosingGuard(TargetGuard):
        async def admit(self, trial_id: str) -> None:
            if trial_id == "a":
                raise ConnectionError("target unavailable")

    trial_target = Target(
        name="memory",
        kind="docker",
        docker_context="memory",
        trusted=True,
        max_concurrency=1,
        grader="memory",
        data_check=(),
        data_check_expect=None,
        transport=None,
    )
    losing = LosingGuard(
        target=trial_target,
        healthy=lambda: True,
        ensure=lambda _budget_s: None,
        reap=lambda _trial_id, _trial_dir: {},
    )
    target = JobTarget(
        Selection(trial=trial_target, grader=trial_target),
        guard=losing if ending == "target_loss" else None,
    )
    return plan, target, files, records, started


@pytest.mark.parametrize("ending", ["empty", "complete", "trial_failure", "target_loss"])
async def test_completion_records_only_finished_trials_and_preserves_abort_reason(
    monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    plan, target, files, records, started = _memory_job(monkeypatch, ending)
    selected = plan.selected_trials()
    job_dir = Path("/memory-job")
    invocation = run.execute(
        JobContext(
            plan=plan,
            job_dir=job_dir,
            job_id="job",
            problems={},
            target=target,
            admission={"decision": "admitted"} if selected else None,
        )
    )
    if ending in {"trial_failure", "target_loss"}:
        error = RuntimeError if ending == "trial_failure" else ConnectionError
        with pytest.raises(error, match="unavailable"):
            await invocation
        completed = records[:1]
    else:
        completed = await invocation
        assert completed == records[: len(selected)]
    result = files[job_dir / JOB_MANIFEST]
    expected_started = {
        "empty": [],
        "complete": ["z", "a", "m"],
        "trial_failure": ["z", "a"],
        "target_loss": ["z"],
    }
    assert started == expected_started[ending]
    assert result["summary"] == {
        "trials": len(completed),
        "valid": sum(record.valid for record in completed),
        "checkpoint_states": (
            {}
            if ending == "empty"
            else {"excluded": 1, "failed": 1, "infra_error": 1, "solved": 1}
            if ending == "complete"
            else {"excluded": 1, "solved": 1}
        ),
    }
    assert list(result["records_manifests"]) == sorted(
        record.trial.trial_id for record in completed
    )
    assert result["selected"] == [trial.trial_id for trial in selected]
    assert result["partial"] is True
    assert result["finished_at"] >= result["started_at"]
    assert result["execution_target"]["effective_concurrency"] == 1
    assert (result["runner"]["commit"], result["runner"]["dirty"]) == ("c0ffee", False)
    assert (job_dir / JOB_LOCK_FILE in files) is bool(plan.lock)
    if selected:
        assert result["admission"]["decision"] == "admitted"
    else:
        assert "admission" not in result
    if ending in {"trial_failure", "target_loss"}:
        assert result["aborted"] == (
            "RuntimeError" if ending == "trial_failure" else "ConnectionError"
        )
    else:
        assert "aborted" not in result


@pytest.mark.parametrize("existing", [None, [], [DETACH_DIR], [DETACH_DIR, "plan.json"], ["other"]])
def test_a_job_never_reuses_existing_work_except_its_detached_launch(
    tmp_path: Path, existing: list[str] | None
) -> None:
    directory = tmp_path / "job"
    if existing is not None:
        directory.mkdir()
        for name in existing:
            (directory / name).mkdir()

    def entries() -> list[str] | None:
        return sorted(path.name for path in directory.iterdir()) if directory.is_dir() else None

    if existing is not None and existing != [DETACH_DIR]:
        with pytest.raises(FileExistsError):
            run.create_job_dir(directory)
        assert entries() == sorted(existing)
    else:
        run.create_job_dir(directory)
        assert entries() == ([] if existing is None else [DETACH_DIR])


def _plan(targets: tuple[ProblemTarget, ...]) -> ExperimentPlan:
    """A plan over ``targets``; nothing else in it is read when problems are admitted."""
    loaded = LoadedExperiment(
        spec=ExperimentSpec.model_construct(),
        path=Path("experiment.yaml"),
        source_sha256="",
        overrides=CliOverrides(),
    )
    return ExperimentPlan(
        loaded=loaded,
        lock_sha256="",
        options=PlanOptions(),
        rule=ProgressionRule(mode="continue", reward_key="reward"),
        arms={},
        problems=targets,
        trials=(),
        pack=PackSpec.model_construct(),
    )


def _imported(name: str) -> ImportedProblem:
    root = Path(name)
    bundle = VerifierBundle(root=root / "bundle", manifest=BundleManifest.model_construct())
    return ImportedProblem(ProblemManifest.model_construct(), root, bundle, root / "references")


@pytest.mark.parametrize("validated", [True, False])
def test_problem_content_is_not_admitted_without_validation(validated: bool) -> None:
    first, second = ProblemId.parse("fixture/first@1"), ProblemId.parse("fixture/second@1")
    targets = (ProblemTarget(4, first, "", ()), ProblemTarget(1, second, "", ()))
    problems = {first: _imported("first"), second: _imported("second")}

    @dataclass(frozen=True)
    class Store(ProblemStore):
        def load(self, problem: ProblemId) -> ImportedProblem:
            return problems[problem]

        def require_validated(
            self, problem: ProblemId, manifest: ProblemManifest
        ) -> ValidationRecord:
            if not validated:
                raise ValueError(f"{problem} is unvalidated")
            return ValidationRecord(Verdict.VALIDATED, "", ())

    store = Store(Path("store"))
    if validated:
        admitted = run.load_problems(_plan(targets), store)
        assert admitted == {4: problems[first], 1: problems[second]}
        assert run.load_problems(_plan(()), store) == {}
    else:
        with pytest.raises(ValueError, match="first@1 is unvalidated"):
            run.load_problems(_plan(targets), store)
