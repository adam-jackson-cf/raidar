"""Synthetic benchmark fixture generation for review-surface development.

Generated experiments are clearly labeled as synthetic: experiment ids and run
ids carry a ``synthetic`` prefix and every persisted payload sets a
``synthetic: true`` marker. Synthetic fixtures must never be treated as real
benchmark evidence; they exist to give the findings/review surface stable,
realistic-shaped data.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from raidar.experiment import ExperimentSummaryInput, create_experiment_summary
from raidar.findings import run_findings_artifact
from raidar.sanitization import sanitized_model_dump_json
from raidar.schemas.events import GateEvent, TraceEvent
from raidar.schemas.scorecard import (
    EvalConfig,
    EvalRun,
    FunctionalScore,
    MetricScore,
    RequirementsCoverageScore,
    Scorecard,
    ScorerResult,
    VerificationStabilityScore,
)

SYNTHETIC_MARKER = "synthetic"
_SCENARIO = "bugfix-ledger-balance"
_REVISION = "v001"
_SKILL_SCENARIO = "skill-benchmark-coding-test"
_SKILL_CORE_METRICS = [
    "functional",
    "code-quality",
    "test-coverage",
    "artifact-checks",
    "verification-stability",
]
_HARNESS = "codex-cli"
_PROFILE = "scorers:bugfix@1:0.88+requirements@1:0.10+resource-efficiency@1:0.02"
_STARTED = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
_METRIC_IDS = [
    "defect-resolution",
    "regression-protection",
    "change-containment",
    "verification-stability",
    "defect-evidence-completeness",
    "requirements-coverage",
    "requirements-adherence",
    "resource-efficiency",
]
_SCORER_IDS = ["bugfix@1", "requirements@1", "resource-efficiency@1"]


def generate_synthetic_benchmark(dest_dir: Path) -> list[Path]:
    """Write labeled synthetic experiments and return their directories."""

    bugfix_low = _experiment_meta(_SCENARIO, _REVISION, _PROFILE, _METRIC_IDS, _SCORER_IDS)
    bugfix_medium = bugfix_low
    skill_v1 = _experiment_meta(
        _SKILL_SCENARIO,
        "v001",
        "scorers:typescript-code-task@1:0.98+resource-efficiency@1:0.02",
        [*_SKILL_CORE_METRICS, "resource-efficiency"],
        ["typescript-code-task@1", "resource-efficiency@1"],
    )
    skill_v3 = _experiment_meta(
        _SKILL_SCENARIO,
        "v003",
        "scorers:typescript-code-task@1:0.78+requirements@1:0.20+resource-efficiency@1:0.02",
        [*_SKILL_CORE_METRICS, "requirements-coverage", "requirements-adherence", "resource-efficiency"],
        ["typescript-code-task@1", "requirements@1", "resource-efficiency@1"],
    )
    experiments = [
        _experiment_dir(dest_dir, "gpt-5.5-low", bugfix_low, _mixed_quality_runs("low")),
        _experiment_dir(dest_dir, "gpt-5.5-medium", bugfix_medium, _clean_runs("medium")),
        _experiment_dir(dest_dir, "gpt-5.5-low", skill_v1, _skill_runs("v001", skill_v1, 0.78)),
        _experiment_dir(dest_dir, "gpt-5.5-low", skill_v3, _skill_runs("v003", skill_v3, 0.91)),
    ]
    return experiments


def _experiment_meta(
    scenario: str,
    revision: str,
    profile: str,
    metric_ids: list[str],
    scorer_ids: list[str],
) -> dict[str, object]:
    return {
        "scenario": scenario,
        "revision": revision,
        "profile": profile,
        "metric_ids": metric_ids,
        "scorer_ids": scorer_ids,
    }


def _experiment_dir(
    dest_dir: Path,
    model_label: str,
    meta: dict[str, object],
    runs: list[EvalRun],
) -> Path:
    dir_name = (
        f"{SYNTHETIC_MARKER}-00000000-000000Z__{meta['scenario']}__{meta['revision']}"
        f"__{_HARNESS}__{model_label}"
    )
    experiment_dir = dest_dir / dir_name
    summary = _summary_payload(meta, runs)
    _write_runs(experiment_dir, runs)
    (experiment_dir / "experiment-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return experiment_dir


def _summary_payload(meta: dict[str, object], runs: list[EvalRun]) -> dict[str, object]:
    summary = create_experiment_summary(
        ExperimentSummaryInput(
            scenario_name=str(meta["scenario"]),
            scenario_revision=str(meta["revision"]),
            harness=_HARNESS,
            model=runs[0].config.model,
            evaluation_profile=str(meta["profile"]),
            metrics=list(meta["metric_ids"]),  # type: ignore[arg-type]
            scorers=list(meta["scorer_ids"]),  # type: ignore[arg-type]
            repeats=len(runs),
            repeat_parallel=1,
            runs=runs,
            started_at=_STARTED,
        )
    )
    config = summary.get("config")
    if isinstance(config, dict):
        config[SYNTHETIC_MARKER] = True
    summary[SYNTHETIC_MARKER] = True
    return summary


def _write_runs(experiment_dir: Path, runs: list[EvalRun]) -> None:
    for run in runs:
        run_dir = experiment_dir / "runs" / run.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(
            sanitized_model_dump_json(run, indent=2) + "\n", encoding="utf-8"
        )
        (run_dir / "findings.json").write_text(
            sanitized_model_dump_json(run_findings_artifact(run), indent=2) + "\n",
            encoding="utf-8",
        )


def _mixed_quality_runs(effort: str) -> list[EvalRun]:
    healthy = [
        _run(f"{SYNTHETIC_MARKER}-low-{index:02d}", effort, duration=120.0 + 10 * index)
        for index in range(1, 4)
    ]
    return [*healthy, _degraded_run(f"{SYNTHETIC_MARKER}-low-04", effort)]


def _clean_runs(effort: str) -> list[EvalRun]:
    return [
        _run(f"{SYNTHETIC_MARKER}-med-{index:02d}", effort, duration=150.0 + 12 * index)
        for index in range(1, 4)
    ]


def _skill_runs(revision: str, meta: dict[str, object], quality: float) -> list[EvalRun]:
    return [
        _skill_run(
            f"{SYNTHETIC_MARKER}-skill-{revision}-{index:02d}",
            revision,
            meta,
            quality=round(quality + 0.01 * (index - 2), 3),
            duration=200.0 - (40.0 if revision == "v003" else 0.0) + 8 * index,
        )
        for index in range(1, 4)
    ]


def _skill_run(
    run_id: str,
    revision: str,
    meta: dict[str, object],
    *,
    quality: float,
    duration: float,
) -> EvalRun:
    scorecard = Scorecard(
        run_id=run_id,
        scenario_name=_SKILL_SCENARIO,
        scenario_revision=revision,
        harness=_HARNESS,
        model=_model("low"),
        starter_root="starter",
        duration_sec=duration,
        functional=FunctionalScore(
            passed=True,
            tests_passed=6,
            tests_total=6,
            build_succeeded=True,
            gates_passed=4,
            gates_total=4,
        ),
        requirements_coverage=RequirementsCoverageScore(
            total_requirements=8,
            satisfied_requirements=8 if revision == "v003" else 7,
            missing_requirement_ids=[] if revision == "v003" else ["req-no-todo"],
        ),
        metadata={
            SYNTHETIC_MARKER: True,
            "run": {"canonical_run_dir": None, "run_json_path": None},
            "process": {"uncached_input_tokens": 26000 if revision == "v003" else 33000},
        },
        metric_scores=[
            MetricScore(metric_id=metric_id, score=quality, passed=quality >= 0.8)
            for metric_id in list(meta["metric_ids"])  # type: ignore[arg-type]
        ],
        scorer_results=_skill_scorer_results(revision, quality),
    )
    return EvalRun(
        id=run_id,
        timestamp=_STARTED.isoformat(),
        config=EvalConfig(
            model=_model("low"),
            harness=_HARNESS,
            scenario_name=_SKILL_SCENARIO,
            scenario_revision=revision,
            starter_root="starter",
            evaluation_profile=str(meta["profile"]),
            scorers=list(meta["scorer_ids"]),  # type: ignore[arg-type]
        ),
        duration_sec=duration,
        terminated_early=False,
        scores=scorecard,
        traces=_skill_trace_events(),
    )


def _skill_scorer_results(revision: str, quality: float) -> list[ScorerResult]:
    if revision == "v003":
        return [
            ScorerResult(
                scorer_id="typescript-code-task",
                version=1,
                category="quality",
                weight=0.78,
                score=quality,
            ),
            ScorerResult(
                scorer_id="requirements", version=1, category="quality", weight=0.20, score=quality
            ),
            ScorerResult(
                scorer_id="resource-efficiency",
                version=1,
                category="efficiency",
                weight=0.02,
                score=0.88,
            ),
        ]
    return [
        ScorerResult(
            scorer_id="typescript-code-task",
            version=1,
            category="quality",
            weight=0.98,
            score=quality,
        ),
        ScorerResult(
            scorer_id="resource-efficiency",
            version=1,
            category="efficiency",
            weight=0.02,
            score=0.8,
        ),
    ]


def _skill_trace_events() -> list[TraceEvent]:
    return [
        TraceEvent(
            timestamp=_at(0),
            event_type="user_prompt",
            data={"content": "Build sumEven with minimal churn."},
        ),
        TraceEvent(
            timestamp=_at(4),
            event_type="file_change",
            data={"file_path": "src/lib/math.ts"},
        ),
        TraceEvent(
            timestamp=_at(6),
            event_type="file_change",
            data={"file_path": "src/test/math.test.ts"},
        ),
        *_command_events(8, "bun run typecheck", 0),
        *_command_events(12, "bun run lint", 0),
        *_command_events(16, "bun run test", 0),
        *_command_events(20, "bun run test:coverage", 0),
    ]


def _model(effort: str) -> str:
    return f"openai/gpt-5.5:{effort}"


def _run(run_id: str, effort: str, *, duration: float) -> EvalRun:
    scorecard = _scorecard(run_id, effort, duration=duration)
    scorecard.metric_scores = _passing_metric_scores()
    scorecard.scorer_results = _scorer_results(0.97, 0.93)
    scorecard.metadata["evidence"] = {
        "retained_files": [
            {
                "path": "evidence/defect-evidence.json",
                "status": "ingested",
                "keys": ["reproduction_note", "regression_tests", "verification_evidence"],
            }
        ]
    }
    return _eval_run(run_id, effort, scorecard, duration=duration)


def _degraded_run(run_id: str, effort: str) -> EvalRun:
    scorecard = _scorecard(run_id, effort, duration=540.0)
    scorecard.functional = FunctionalScore(
        passed=False,
        tests_passed=7,
        tests_total=9,
        build_succeeded=True,
        gates_passed=2,
        gates_total=4,
    )
    scorecard.verification_stability = VerificationStabilityScore(
        total_gate_failures=3,
        unique_failure_categories=2,
        repeat_failures=1,
    )
    scorecard.requirements_coverage = RequirementsCoverageScore(
        total_requirements=5,
        satisfied_requirements=3,
        missing_requirement_ids=["req-repro-test-enabled", "req-defect-evidence-retained"],
    )
    scorecard.metric_scores = _failing_metric_scores()
    scorecard.scorer_results = _scorer_results(0.46, 0.40)
    scorecard.metadata["process"] = {
        "missing_required_verification_commands": 1,
        "required_verification_first_pass": {"bun run test:coverage": "missing"},
    }
    scorecard.metadata["evidence"] = {
        "retained_files": [
            {"path": "evidence/defect-evidence.json", "status": "missing", "keys": []}
        ]
    }
    gates = [
        GateEvent(
            timestamp=_STARTED.isoformat(),
            gate_name="test",
            command="bun run test",
            exit_code=1,
            stdout="",
            stderr="2 tests failed: debit entries are still added to the balance",
            failure_category="test",
        ),
        GateEvent(
            timestamp=_STARTED.isoformat(),
            gate_name="lint",
            command="bun run lint",
            exit_code=1,
            stdout="",
            stderr="import order violation in src/test/ledger.test.ts",
            failure_category="lint",
            is_repeat=False,
        ),
    ]
    return _eval_run(run_id, effort, scorecard, duration=540.0, gates=gates, fix_succeeded=False)


def _scorecard(run_id: str, effort: str, *, duration: float) -> Scorecard:
    return Scorecard(
        run_id=run_id,
        scenario_name=_SCENARIO,
        scenario_revision=_REVISION,
        harness=_HARNESS,
        model=_model(effort),
        starter_root="starter",
        duration_sec=duration,
        functional=FunctionalScore(
            passed=True,
            tests_passed=12,
            tests_total=12,
            build_succeeded=True,
            gates_passed=4,
            gates_total=4,
        ),
        requirements_coverage=RequirementsCoverageScore(
            total_requirements=5,
            satisfied_requirements=5,
        ),
        metadata={
            SYNTHETIC_MARKER: True,
            "run": {"canonical_run_dir": None, "run_json_path": None},
            "process": {"uncached_input_tokens": 42000},
        },
    )


def _eval_run(
    run_id: str,
    effort: str,
    scorecard: Scorecard,
    *,
    duration: float,
    gates: list[GateEvent] | None = None,
    fix_succeeded: bool = True,
) -> EvalRun:
    return EvalRun(
        id=run_id,
        timestamp=_STARTED.isoformat(),
        config=EvalConfig(
            model=_model(effort),
            harness=_HARNESS,
            scenario_name=_SCENARIO,
            scenario_revision=_REVISION,
            starter_root="starter",
            evaluation_profile=_PROFILE,
            scorers=_SCORER_IDS,
        ),
        duration_sec=duration,
        terminated_early=False,
        scores=scorecard,
        traces=_trace_events(fix_succeeded=fix_succeeded),
        gate_history=gates or [],
    )


def _at(offset_sec: float) -> str:
    return datetime(2026, 1, 1, 0, 0, int(offset_sec), tzinfo=UTC).isoformat()


def _command_events(offset_sec: float, command: str, exit_code: int) -> list[TraceEvent]:
    return [
        TraceEvent(timestamp=_at(offset_sec), event_type="bash_command", data={"command": command}),
        TraceEvent(
            timestamp=_at(offset_sec + 2),
            event_type="gate_result",
            data={"status": "completed" if exit_code == 0 else "failed", "exit_code": exit_code},
        ),
    ]


def _trace_events(*, fix_succeeded: bool) -> list[TraceEvent]:
    events = [
        TraceEvent(
            timestamp=_at(0),
            event_type="user_prompt",
            data={"content": "Fix bug RAID-1042 in the ledger utility with minimal changes."},
        ),
        TraceEvent(
            timestamp=_at(4),
            event_type="assistant_message",
            data={"content": "Re-enabling the skipped reproduction test to confirm the defect."},
        ),
        TraceEvent(
            timestamp=_at(6),
            event_type="file_change",
            data={"file_path": "src/test/ledger.test.ts"},
        ),
        *_command_events(8, "bun run test", 1),
        TraceEvent(
            timestamp=_at(12),
            event_type="assistant_message",
            data={"content": "Reproduced: expected 6500 to be 3500. Fixing debit handling."},
        ),
        TraceEvent(
            timestamp=_at(14),
            event_type="file_change",
            data={"file_path": "src/lib/ledger.ts"},
        ),
        TraceEvent(
            timestamp=_at(16),
            event_type="file_change",
            data={"file_path": "src/test/ledger.regression.test.ts"},
        ),
        *_command_events(18, "bun run test", 0 if fix_succeeded else 1),
        *_command_events(22, "bun run typecheck", 0),
        *_command_events(26, "bun run lint", 0 if fix_succeeded else 1),
        *_command_events(30, "bun run test:coverage", 0 if fix_succeeded else 1),
    ]
    if fix_succeeded:
        events.append(
            TraceEvent(
                timestamp=_at(34),
                event_type="file_change",
                data={"file_path": "evidence/defect-evidence.json"},
            )
        )
        events.append(
            TraceEvent(
                timestamp=_at(36),
                event_type="assistant_message",
                data={"content": "All gates pass; defect evidence retained."},
            )
        )
    return events


def _passing_metric_scores() -> list[MetricScore]:
    return [
        MetricScore(metric_id="defect-resolution", score=1.0, passed=True),
        MetricScore(metric_id="regression-protection", score=1.0, passed=True),
        MetricScore(metric_id="change-containment", score=1.0, passed=True),
        MetricScore(metric_id="verification-stability", score=1.0, passed=True),
        MetricScore(metric_id="defect-evidence-completeness", score=1.0, passed=True),
        MetricScore(metric_id="requirements-coverage", score=1.0, passed=True),
        MetricScore(
            metric_id="requirements-adherence",
            score=0.95,
            passed=True,
            judge_output={"verdict": "satisfied"},
            evidence="judge verdict satisfied with full evidence bundle",
        ),
        MetricScore(metric_id="resource-efficiency", score=0.9, passed=True),
    ]


def _failing_metric_scores() -> list[MetricScore]:
    return [
        MetricScore(
            metric_id="defect-resolution",
            score=0.46,
            passed=False,
            evidence="direct: functional execution capped by defect-linked requirement checks",
        ),
        MetricScore(
            metric_id="regression-protection",
            score=0.5,
            passed=False,
            missing_patterns=["behavior-specific regression tests"],
            evidence="proxy: test file inventory cannot prove failure-before-pass replay",
        ),
        MetricScore(metric_id="change-containment", score=0.85, passed=True),
        MetricScore(metric_id="verification-stability", score=0.4, passed=False),
        MetricScore(
            metric_id="defect-evidence-completeness",
            score=0.5,
            passed=False,
            missing_patterns=["reproduction note", "regression test evidence"],
        ),
        MetricScore(metric_id="requirements-coverage", score=0.6, passed=False),
        MetricScore(
            metric_id="requirements-adherence",
            score=0.3,
            passed=False,
            judge_output={"verdict": "unsatisfied"},
            evidence="judge verdict unsatisfied: defect evidence missing",
        ),
        MetricScore(metric_id="resource-efficiency", score=0.5, passed=False),
    ]


def _scorer_results(quality: float, efficiency: float) -> list[ScorerResult]:
    return [
        ScorerResult(scorer_id="bugfix", version=1, category="quality", weight=0.88, score=quality),
        ScorerResult(
            scorer_id="requirements", version=1, category="quality", weight=0.10, score=quality
        ),
        ScorerResult(
            scorer_id="resource-efficiency",
            version=1,
            category="efficiency",
            weight=0.02,
            score=efficiency,
        ),
    ]


def main() -> None:
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../experiments/benchmarks")
    dest.mkdir(parents=True, exist_ok=True)
    for experiment_dir in generate_synthetic_benchmark(dest):
        print(f"Wrote synthetic experiment: {experiment_dir}")


if __name__ == "__main__":
    main()
