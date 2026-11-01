"""Job completion keeps a truthful manifest even when admission or a trial fails."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raidar.contract.job_layout import DETACH_DIR, JOB_LOCK_FILE, JOB_MANIFEST
from raidar.contract.trial import CheckpointState
from raidar.engine.job import manifest, run
from raidar.engine.target.config import Selection, Target
from raidar.engine.target.job import JobTarget


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
    monkeypatch.setattr(manifest, "runner_provenance", dict)
    monkeypatch.setattr(manifest, "recipe_digests", lambda *_args: {})
    monkeypatch.setattr(manifest, "effective_limits", lambda *_args: {})
    monkeypatch.setattr(run, "create_job_dir", lambda _path: None)
    monkeypatch.setattr(run, "seal_trial", lambda trial_dir, *_args: f"sealed-{trial_dir.name}")

    async def inline(function: Any, *args: Any) -> Any:
        return function(*args)

    monkeypatch.setattr(run.asyncio, "to_thread", inline)
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

    class LosingTarget:
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
    target = JobTarget(
        Selection(trial=trial_target, grader=trial_target),
        guard=LosingTarget() if ending == "target_loss" else None,
    )
    return plan, target, files, records, started


@pytest.mark.parametrize("ending", ["empty", "complete", "trial_failure", "target_loss"])
async def test_completion_records_only_finished_trials_and_preserves_abort_reason(
    monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    plan, target, files, records, started = _memory_job(monkeypatch, ending)
    selected = plan.selected_trials()
    job_dir = Path("/memory-job")
    invocation = run.run_job(
        plan,
        {},
        job_dir=job_dir,
        job_id="job",
        target=target,
        admission={"decision": "admitted"} if selected else None,
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
    existing: list[str] | None,
) -> None:
    class Directory:
        entries = existing

        def is_dir(self) -> bool:
            return self.entries is not None

        def iterdir(self) -> list[Path]:
            return [Path(name) for name in self.entries or []]

        def mkdir(self, **_kwargs: Any) -> None:
            if self.entries is not None:
                raise FileExistsError("already contains work")
            self.entries = []

    directory = Directory()
    if existing is not None and existing != [DETACH_DIR]:
        with pytest.raises(FileExistsError):
            run.create_job_dir(directory)
        assert directory.entries == existing
    else:
        run.create_job_dir(directory)
        assert directory.entries == ([] if existing is None else [DETACH_DIR])


@pytest.mark.parametrize("validated", [True, False])
def test_problem_content_is_not_admitted_without_validation(validated: bool) -> None:
    targets = [SimpleNamespace(index=4, ref="first"), SimpleNamespace(index=1, ref="second")]
    problems = {
        ref: SimpleNamespace(manifest=SimpleNamespace(validated=validated))
        for ref in ("first", "second")
    }

    class Store:
        def load(self, ref: str) -> Any:
            return problems[ref]

        def require_validated(self, ref: str, record: Any) -> None:
            if not record.validated:
                raise ValueError(f"{ref} is unvalidated")

    if validated:
        admitted = run.load_problems(SimpleNamespace(problems=targets), Store())
        assert admitted == {4: problems["first"], 1: problems["second"]}
        assert run.load_problems(SimpleNamespace(problems=[]), Store()) == {}
    else:
        with pytest.raises(ValueError, match="first is unvalidated"):
            run.load_problems(SimpleNamespace(problems=targets), Store())
