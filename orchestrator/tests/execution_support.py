"""Fixtures for execution (WS-D) tests: validated problems, locked experiments, trials.

- ``validated_fixture``: the WS-B ``fixture/adder`` store, validated with the
  local grader (no Docker) so it locks. Its task is not pack-bound (no compose
  override), so experiments over it set ``environment.compose_init: false``.
- ``imported_pack_fixture``: the real WS-B importer over the fake SCB dataset,
  so the task is pack-bound exactly like a pilot problem (pack image, ``agent``
  user, compose ``init: true``, per-step ``/app`` artifacts), validated with the
  Docker grader. Used by the Docker-gated perf fixture.
- ``write_trial``: a synthetic trial directory mirroring what Harbor and the
  Raidar verifier write, for classification, progression and phase tests.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from raidar.design.experiments.loader import LoadedExperiment, load_experiment
from raidar.design.experiments.lock import (
    build_lock,
    default_lock_path,
    default_resolvers,
    render_lock,
)
from raidar.design.problems.importer import import_problem
from raidar.design.problems.models import ProblemId
from raidar.design.problems.store import ProblemStore
from raidar.design.problems.validator import ValidateOptions, validate_problem, write_report
from raidar.engine.trials.reaper import ReapReport, write_reap_report
from raidar.grading.leak import LeakReport
from raidar.grading.models import CaseGroup, CaseResult, CaseStatus, InfraFailure, VerifierGrade
from raidar.grading.records import (
    BundleRef,
    CoverageRef,
    GraderRef,
    SnapshotRef,
    StepRecord,
    step_record_dir,
    write_pre_agent_leak,
    write_step_record,
)
from raidar.grading.verifier import step_rewards
from tests.experiment_spec_support import experiment_document, write_experiment
from tests.problems_support import (
    POSITIVE_REF,
    build_fake_scb_repo,
    build_fixture_store,
    fake_store,
    record_validation,
)

T0 = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class ImportedFixture:
    store: ProblemStore
    ref: str


def validated_fixture(root: Path) -> ImportedFixture:
    store = build_fixture_store(root / "problems")
    record_validation(store, POSITIVE_REF, root / "validation")
    return ImportedFixture(store, POSITIVE_REF)


def imported_pack_fixture(root: Path, docker_context: str) -> ImportedFixture:
    repo, commit = build_fake_scb_repo(root / "src")
    store = fake_store(root / "problems", commit)
    problem = ProblemId.parse(f"scb/adder@{commit[:12]}")
    import_problem(store, problem, source_checkout=repo)
    report = asyncio.run(
        validate_problem(
            store,
            problem,
            # Two repeats: a usable verdict needs the repeat_identical checks (dab09a0).
            ValidateOptions(repeats=2, concurrency=2, docker_context=docker_context),
            work_root=root,
        )
    )
    write_report(report, store.validation_path(problem))
    return ImportedFixture(store, str(problem))


def locked_experiment(
    root: Path, ref: str, **changes: Any
) -> tuple[LoadedExperiment, dict[str, Any], Path]:
    """Write and lock an experiment over ``ref`` (RAIDAR_PROBLEMS_ROOT must name the store)."""
    document = experiment_document(problems=[{"ref": ref}], **changes)
    path = write_experiment(root, document)
    loaded = load_experiment(path)
    lock_path = default_lock_path(path)
    rendered = render_lock(build_lock(loaded, lock_path=lock_path, resolvers=default_resolvers()))
    lock_path.write_bytes(rendered)
    return loaded, json.loads(rendered), lock_path


# ------------------------------------------------------------ synthetic trials
@dataclass(frozen=True)
class StepOutcome:
    """What one executed step leaves behind."""

    name: str
    passed: bool | None = True  # None: no verifier record (verifier failed closed)
    infra: str | None = None
    leaked: bool = False
    exception: str | None = None
    reset_ok: bool = True


def verifier_grade(step: str, outcome: StepOutcome) -> VerifierGrade:
    status = CaseStatus.PASSED if outcome.passed else CaseStatus.FAILED
    cases = (
        CaseResult(id=f"{step}::a", group=CaseGroup.CORE, checkpoint_origin=step, status=status),
        CaseResult(
            id=f"{step}::b",
            group=CaseGroup.FUNCTIONALITY,
            checkpoint_origin=step,
            status=CaseStatus.PASSED,
        ),
    )
    infra = InfraFailure(code=outcome.infra) if outcome.infra else None
    return VerifierGrade(step=step, checkpoint=step, cases=cases, infra_failure=infra)


def _record(step: str, outcome: StepOutcome, trial_dir: Path) -> StepRecord:
    grade = verifier_grade(step, outcome)
    rewards = step_rewards(grade.rewards(), leaked=outcome.leaked)
    return StepRecord(
        step=step,
        checkpoint=step,
        bundle=BundleRef(root=str(trial_dir), digest="b" * 64, problem="scb/adder@fixture"),
        snapshot=SnapshotRef(
            requested="artifact",
            used="artifact",
            digest=f"{step}-digest",
            stored_path=f"steps/{step}/artifacts/app",
        ),
        grader=GraderRef(image="grader:x", environment_id="env", image_built=False, timeout_s=900),
        grade=grade,
        summary=grade.summary(),
        coverage=CoverageRef(enabled=False),
        leak_check=LeakReport(clean=not outcome.leaked, seconds=0.4),
        pre_agent_leak_check=LeakReport(clean=True, seconds=0.5),
        rewards=rewards,
        timings={
            "snapshot_transfer_s": 0.01,
            "verifier_plumbing_s": 0.2,
            "grader_image_s": 0.3,
            "leak_check_s": 0.4,
            "oracle_run_s": 5.0,
        },
    )


def _iso(offset_s: float) -> str:
    return (T0 + timedelta(seconds=offset_s)).isoformat()


def _write_reset(trial_dir: Path, step: str, ok: bool) -> None:
    agent_dir = trial_dir / "steps" / step / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    if ok:
        report = {
            "kind": "raidar-step-reset",
            "duration_ms": 240,
            "error": None,
            "killed": [],
            "residual": [],
            "state": [],
        }
        (agent_dir / "raidar-reset.json").write_text(json.dumps(report))


def write_trial(
    trial_dir: Path,
    outcomes: Sequence[StepOutcome],
    *,
    agent_version: str = "0.156.1",
    trial_exception: str | None = None,
) -> Path:
    """A Harbor multi-step trial dir: result.json, verifier records, reset reports.

    Timeline (seconds): env setup 0-10, agent setup 10-11; each step takes 20 s
    (2 s reset gap incl. a 0.5 s pre-agent scan, 10 s agent, 3 s collection,
    5 s verifier); teardown 3 s.
    """
    trial_dir.mkdir(parents=True, exist_ok=True)
    step_results = []
    clock = 11.0
    for outcome in outcomes:
        a_start, a_end = clock + 2, clock + 12
        v_start, v_end = a_end + 3, a_end + 8
        item: dict[str, Any] = {
            "step_name": outcome.name,
            "agent_result": {
                "n_input_tokens": 100,
                "n_cache_tokens": 10,
                "n_output_tokens": 5,
                "cost_usd": 0.01,
            },
            "agent_execution": {"started_at": _iso(a_start), "finished_at": _iso(a_end)},
            "verifier": {"started_at": _iso(v_start), "finished_at": _iso(v_end)},
            "exception_info": (
                {
                    "exception_type": outcome.exception,
                    "exception_message": "x",
                    "exception_traceback": "",
                    "occurred_at": _iso(a_end),
                }
                if outcome.exception
                else None
            ),
        }
        step_results.append(item)
        _write_reset(trial_dir, outcome.name, outcome.reset_ok)
        record_dir = step_record_dir(trial_dir, outcome.name)
        write_pre_agent_leak(record_dir, LeakReport(clean=True, seconds=0.5))
        write_reap_report(record_dir, ReapReport("agent", (), (), None))
        if outcome.passed is not None:
            write_step_record(record_dir, _record(outcome.name, outcome, trial_dir))
        clock = v_end
    result = {
        "agent_info": {"name": "codex", "version": agent_version},
        "started_at": _iso(0),
        "finished_at": _iso(clock + 3),
        "environment_setup": {"started_at": _iso(0), "finished_at": _iso(10)},
        "agent_setup": {"started_at": _iso(10), "finished_at": _iso(11)},
        "step_results": step_results,
        "exception_info": (
            {
                "exception_type": trial_exception,
                "exception_message": "x",
                "exception_traceback": "",
                "occurred_at": _iso(clock),
            }
            if trial_exception
            else None
        ),
    }
    (trial_dir / "result.json").write_text(json.dumps(result))
    return trial_dir
