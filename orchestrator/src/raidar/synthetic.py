"""Synthetic benchmark fixture generation for review-surface development.

Generated experiments are clearly labeled as synthetic: experiment ids and run
ids carry a ``synthetic`` prefix and every persisted payload sets a
``synthetic: true`` marker. Synthetic fixtures must never be treated as real
benchmark evidence; they exist to give the findings/review surface stable,
realistic-shaped data.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from raidar.experiment import ExperimentSummaryInput, create_experiment_summary
from raidar.findings import run_findings_artifact
from raidar.review_projection import persist_experiment_review_projection
from raidar.review_scenario_projection import scenario_review_contract
from raidar.sanitization import sanitized_model_dump_json
from raidar.schemas.events import GateEvent, TraceEvent
from raidar.schemas.scenario import ScenarioDefinition
from raidar.schemas.scorecard import (
    EvalConfig,
    EvalRun,
    Scorecard,
)
from raidar.scorers.registry import scenario_scorers, scorer_evaluation_profile
from raidar.synthetic_scorer_inputs import SyntheticEvidence, build_synthetic_scorecard

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
_STARTED = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Spec:
    """An agent spec: the harness/model pairing a scenario is run against."""

    harness: str
    model: str
    label: str  # short slug used in the experiment directory name
    reasoning_effort: str | None = None


_GPT_LOW = Spec("codex-cli", "openai/gpt-5.5", "gpt-5.5-low", "low")
_GPT_MEDIUM = Spec("codex-cli", "openai/gpt-5.5", "gpt-5.5-medium", "medium")
_GPT_HIGH = Spec("codex-cli", "openai/gpt-5.5", "gpt-5.5-high", "high")
_CLAUDE = Spec("claude-code", "anthropic/claude-sonnet-4.6", "claude-sonnet-4.6")


def _fixture_scenario(
    name: str,
    revision: str,
    scorer_weights: list[tuple[str, float]],
    *,
    display_name: str,
    task_kind: str,
    workspace_state: str,
    starting_conditions: list[str],
    parent_revision: str | None = None,
) -> ScenarioDefinition:
    requirements = [
        {
            "id": f"req-{index}",
            "description": f"Synthetic requirement {index}",
            "check": {
                "type": "file_exists",
                "pattern": f"synthetic/{index}.txt",
                "description": f"Synthetic evidence {index}",
            },
        }
        for index in range(1, 9)
    ]
    return ScenarioDefinition.model_validate(
        {
            "name": name,
            "display_name": display_name,
            "scenario_revision": revision,
            "parent_revision": parent_revision,
            "description": f"Typed synthetic fixture for {name} {revision}",
            "difficulty": "medium",
            "task_kind": task_kind,
            "delivery_mode": "direct_implementation",
            "workspace_state": workspace_state,
            "starting_conditions": starting_conditions,
            "languages": ["TypeScript"],
            "evaluation_role": "benchmark",
            "catalog_status": "calibration",
            "family_id": None,
            "variant_axes": {},
            "timeout_sec": 300,
            "environment": {"kind": "stack_preset", "id": "node:20"},
            "starter": {"root": "starter"},
            "verification": {
                "coverage_threshold": 0.8,
                "min_quality_score": 0.0,
                "required_commands": [["synthetic", "verify"]],
            },
            "requirements": {"items": requirements},
            "scorers": [
                {"id": scorer_id, "version": 1, "weight": weight}
                for scorer_id, weight in scorer_weights
            ],
            "prompt": {"entry": "prompt/task.md"},
        }
    )


_BUGFIX_SCENARIO = _fixture_scenario(
    _SCENARIO,
    _REVISION,
    [("bugfix", 0.88), ("requirements", 0.10), ("resource-efficiency", 0.02)],
    display_name="Ledger Balance Defect Repair",
    task_kind="defect_repair",
    workspace_state="existing",
    starting_conditions=["known_defect"],
)


def _skill_scenario(revision: str) -> ScenarioDefinition:
    if revision == "v001":
        weights = [("typescript-code-task", 0.98), ("resource-efficiency", 0.02)]
    elif revision == "v002":
        weights = [
            ("typescript-code-task", 0.85),
            ("requirements", 0.13),
            ("resource-efficiency", 0.02),
        ]
    else:
        weights = [
            ("typescript-code-task", 0.78),
            ("requirements", 0.20),
            ("resource-efficiency", 0.02),
        ]
    parent = None if revision == "v001" else f"v{int(revision[1:]) - 1:03d}"
    return _fixture_scenario(
        _SKILL_SCENARIO,
        revision,
        weights,
        display_name="Skill Benchmark Coding Test",
        task_kind="feature_implementation",
        workspace_state="existing",
        starting_conditions=[],
        parent_revision=parent,
    )


def generate_synthetic_benchmark(dest_dir: Path) -> list[Path]:
    """Write labeled synthetic experiments and return their directories."""

    return [*_bugfix_experiments(dest_dir), *_skill_experiments(dest_dir)]


def _bugfix_experiments(dest_dir: Path) -> list[Path]:
    """One scenario revision delivered by three agent specs with distinct means.

    Exercises best-first ordering, the Δ-vs-best comparison framing, and a
    volatile spec whose degraded run drags its mean below the others.
    """
    meta = _experiment_meta(_BUGFIX_SCENARIO)
    return [
        _experiment_dir(
            dest_dir, _GPT_MEDIUM, meta, _clean_runs(_GPT_MEDIUM, quality=0.97, count=5)
        ),
        _experiment_dir(dest_dir, _GPT_HIGH, meta, _clean_runs(_GPT_HIGH, quality=0.92, count=5)),
        _experiment_dir(dest_dir, _GPT_LOW, meta, _mixed_quality_runs(_GPT_LOW)),
    ]


def _skill_experiments(dest_dir: Path) -> list[Path]:
    """Two specs across three revisions, with a leadership crossover at v003.

    Exercises per-revision comparison and revision-movement deltas for each
    spec as the scenario contract tightens (more requirements weight).
    """
    quality = {
        ("v001", _GPT_LOW): 0.78,
        ("v001", _CLAUDE): 0.74,
        ("v002", _GPT_LOW): 0.85,
        ("v002", _CLAUDE): 0.83,
        ("v003", _GPT_LOW): 0.91,
        ("v003", _CLAUDE): 0.95,
    }
    dirs: list[Path] = []
    for revision in ("v001", "v002", "v003"):
        meta = _skill_meta(revision)
        for spec in (_GPT_LOW, _CLAUDE):
            runs = _skill_runs(spec, revision, meta, quality[(revision, spec)])
            dirs.append(_experiment_dir(dest_dir, spec, meta, runs))
    return dirs


def _skill_meta(revision: str) -> dict[str, object]:
    return _experiment_meta(_skill_scenario(revision))


def _str_list(meta: dict[str, object], key: str) -> list[str]:
    value = meta[key]
    assert isinstance(value, list)
    return value


def _experiment_meta(scenario: ScenarioDefinition) -> dict[str, object]:
    return {
        "scenario": scenario.name,
        "revision": scenario.scenario_revision,
        "profile": scorer_evaluation_profile(scenario),
        "metric_ids": scenario.metric_ids(),
        "scorer_ids": scenario_scorers(scenario),
        "scenario_definition": scenario,
    }


def _experiment_dir(
    dest_dir: Path,
    spec: Spec,
    meta: dict[str, object],
    runs: list[EvalRun],
) -> Path:
    dir_name = (
        f"{SYNTHETIC_MARKER}-00000000-000000Z__{meta['scenario']}__{meta['revision']}"
        f"__{spec.harness}__{spec.label}"
    )
    experiment_dir = dest_dir / dir_name
    # Regeneration is idempotent: clear any prior synthetic payload so renamed
    # or removed runs never linger as stale projections in the review surface.
    shutil.rmtree(experiment_dir, ignore_errors=True)
    summary = _summary_payload(spec, meta, runs)
    _write_runs(experiment_dir, runs)
    (experiment_dir / "experiment-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    persist_experiment_review_projection(experiment_dir, summary, runs)
    return experiment_dir


def _summary_payload(spec: Spec, meta: dict[str, object], runs: list[EvalRun]) -> dict[str, object]:
    scenario = meta["scenario_definition"]
    assert isinstance(scenario, ScenarioDefinition)
    previous = (
        _skill_scenario(scenario.parent_revision)
        if scenario.name == _SKILL_SCENARIO and scenario.parent_revision
        else None
    )
    summary = create_experiment_summary(
        ExperimentSummaryInput(
            scenario_name=str(meta["scenario"]),
            scenario_revision=str(meta["revision"]),
            harness=spec.harness,
            model=spec.model,
            reasoning_effort=spec.reasoning_effort,
            evaluation_profile=str(meta["profile"]),
            metrics=_str_list(meta, "metric_ids"),
            scorers=_str_list(meta, "scorer_ids"),
            repeats=len(runs),
            repeat_parallel=1,
            runs=runs,
            started_at=_STARTED,
            review=scenario_review_contract(
                scenario,
                previous_scenario=previous,
            ),
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


def _bugfix_run_id(spec: Spec, index: int) -> str:
    return f"{SYNTHETIC_MARKER}-bugfix-{spec.label}-{index:02d}"


def _mixed_quality_runs(spec: Spec) -> list[EvalRun]:
    """Three clean runs, one degraded run, and one unscored run.

    The unscored run exercises the rerun/unscored path: it is excluded from
    the composite stats and surfaces as an explicit "needs rerun" state.
    """
    healthy = [
        _run(_bugfix_run_id(spec, index), spec, duration=120.0 + 10 * index, quality=0.97)
        for index in range(1, 4)
    ]
    return [
        *healthy,
        _degraded_run(_bugfix_run_id(spec, 4), spec),
        _unscored_run(_bugfix_run_id(spec, 5), spec),
    ]


def _unscored_run(run_id: str, spec: Spec) -> EvalRun:
    reasons = [
        "Scoring did not complete: the harness exited before verification finished.",
        "Re-run this scenario to obtain a score.",
    ]
    scorecard = build_synthetic_scorecard(
        run_id,
        spec,
        _BUGFIX_SCENARIO,
        duration=300.0,
        evidence=SyntheticEvidence(default_score=0.0),
        process=_bugfix_token_spend(spec, run_id),
        unscored=True,
        unscored_reasons=reasons,
    )
    return _eval_run(run_id, spec, scorecard, duration=300.0, fix_succeeded=False)


def _clean_runs(spec: Spec, *, quality: float, count: int) -> list[EvalRun]:
    efficiency = 0.93 if quality >= 0.95 else 0.85
    return [
        _run(
            _bugfix_run_id(spec, index),
            spec,
            duration=150.0 + 12 * index,
            quality=quality,
            efficiency=efficiency,
        )
        for index in range(1, count + 1)
    ]


def _skill_runs(
    spec: Spec, revision: str, meta: dict[str, object], quality: float
) -> list[EvalRun]:
    return [
        _skill_run(
            f"{SYNTHETIC_MARKER}-skill-{revision}-{spec.label}-{index:02d}",
            spec,
            revision,
            meta,
            quality=round(quality + 0.01 * (index - 2), 3),
            duration=200.0 - (40.0 if revision == "v003" else 0.0) + 8 * index,
            index=index,
        )
        for index in range(1, 4)
    ]


def _skill_token_spend(spec: Spec, revision: str, index: int) -> dict[str, int]:
    """Synthetic token spend with enough variation to exercise chart scaling."""

    base_input = {"v001": 36000, "v002": 31000, "v003": 26000}.get(revision, 30000)
    run_offset = (index - 2) * 650
    if spec is _CLAUDE:
        return {
            "uncached_input_tokens": base_input + 1200 + run_offset,
            "output_tokens": 5400 + (index - 2) * 260,
        }
    return {
        "uncached_input_tokens": base_input - 900 + run_offset,
        "output_tokens": 3900 + (index - 2) * 220,
    }


def _bugfix_token_spend(spec: Spec, run_id: str) -> dict[str, int]:
    """Synthetic bugfix token spend by model effort and run ordinal."""

    try:
        index = int(run_id.rsplit("-", 1)[1])
    except ValueError:
        index = 1

    if spec is _GPT_HIGH:
        effort_input = 9000
        effort_output = 1800
    elif spec is _GPT_MEDIUM:
        effort_input = 4500
        effort_output = 900
    else:
        effort_input = 0
        effort_output = 0

    return {
        "uncached_input_tokens": 36000 + effort_input + index * 850,
        "output_tokens": 4200 + effort_output + index * 180,
    }


def _skill_run(
    run_id: str,
    spec: Spec,
    revision: str,
    meta: dict[str, object],
    *,
    quality: float,
    duration: float,
    index: int,
) -> EvalRun:
    scenario = meta["scenario_definition"]
    assert isinstance(scenario, ScenarioDefinition)
    requirement_score, matched_count, missing = _skill_requirements(revision)
    process = _skill_token_spend(spec, revision, index)
    scorecard = build_synthetic_scorecard(
        run_id,
        spec,
        scenario,
        duration=duration,
        evidence=SyntheticEvidence(
            default_score=quality,
            metric_overrides={
                "requirements-coverage": requirement_score,
                "resource-efficiency": 0.88,
            },
            matched_counts={"requirements-coverage": matched_count},
            missing_patterns={"requirements-coverage": missing},
        ),
        process=process,
    )
    return EvalRun(
        id=run_id,
        timestamp=_STARTED.isoformat(),
        config=EvalConfig(
            model=spec.model,
            reasoning_effort=spec.reasoning_effort,
            harness=spec.harness,
            scenario_name=_SKILL_SCENARIO,
            scenario_revision=revision,
            starter_root="starter",
            evaluation_profile=str(meta["profile"]),
            scorers=_str_list(meta, "scorer_ids"),
        ),
        duration_sec=duration,
        terminated_early=False,
        scores=scorecard,
        traces=_skill_trace_events(),
    )


def _skill_requirements(revision: str) -> tuple[float, int, list[str]]:
    satisfied = {"v001": 6, "v002": 7}.get(revision, 8)
    missing = {
        "v001": ["req-no-todo", "req-error-paths"],
        "v002": ["req-no-todo"],
    }.get(revision, [])
    return satisfied / 8, satisfied, missing


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


def _run(
    run_id: str,
    spec: Spec,
    *,
    duration: float,
    quality: float = 0.97,
    efficiency: float = 0.93,
) -> EvalRun:
    scorecard = build_synthetic_scorecard(
        run_id,
        spec,
        _BUGFIX_SCENARIO,
        duration=duration,
        evidence=SyntheticEvidence(
            default_score=quality,
            metric_overrides={
                "requirements-adherence": 0.95,
                "resource-efficiency": efficiency,
            },
            matched_counts={"requirements-coverage": 5},
            judge_outputs={"requirements-adherence": {"verdict": "satisfied"}},
            evidence={
                "requirements-adherence": "judge verdict satisfied with full evidence bundle"
            },
        ),
        process=_bugfix_token_spend(spec, run_id),
    )
    scorecard.metadata["evidence"] = {
        "retained_files": [
            {
                "path": "evidence/defect-evidence.json",
                "status": "ingested",
                "keys": ["reproduction_note", "regression_tests", "verification_evidence"],
            }
        ]
    }
    return _eval_run(run_id, spec, scorecard, duration=duration)


def _degraded_run(run_id: str, spec: Spec) -> EvalRun:
    process = {
        **_bugfix_token_spend(spec, run_id),
        "command_count": 7,
        "failed_command_count": 3,
        "verification_rounds": 3,
        "repeated_verification_failures": 1,
        "missing_required_verification_commands": 1,
        "required_verification_first_pass": {"bun run test:coverage": "missing"},
    }
    scorecard = build_synthetic_scorecard(
        run_id,
        spec,
        _BUGFIX_SCENARIO,
        duration=540.0,
        evidence=_failing_evidence(),
        process=process,
    )
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
    return _eval_run(run_id, spec, scorecard, duration=540.0, gates=gates, fix_succeeded=False)


def _eval_run(
    run_id: str,
    spec: Spec,
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
            model=spec.model,
            reasoning_effort=spec.reasoning_effort,
            harness=spec.harness,
            scenario_name=_SCENARIO,
            scenario_revision=_REVISION,
            starter_root="starter",
            evaluation_profile=scorer_evaluation_profile(_BUGFIX_SCENARIO),
            scorers=scenario_scorers(_BUGFIX_SCENARIO),
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


def _failing_evidence() -> SyntheticEvidence:
    return SyntheticEvidence(
        default_score=0.46,
        metric_overrides={
            "defect-resolution": 0.46,
            "regression-protection": 0.5,
            "change-containment": 0.85,
            "verification-stability": 0.4,
            "defect-evidence-completeness": 0.5,
            "requirements-coverage": 0.6,
            "requirements-adherence": 0.3,
            "resource-efficiency": 0.5,
        },
        matched_counts={"requirements-coverage": 3},
        missing_patterns={
            "regression-protection": ["behavior-specific regression tests"],
            "defect-evidence-completeness": [
                "reproduction note",
                "regression test evidence",
            ],
            "requirements-coverage": [
                "req-repro-test-enabled",
                "req-defect-evidence-retained",
            ],
        },
        evidence={
            "defect-resolution": (
                "direct: functional execution capped by defect-linked requirement checks"
            ),
            "regression-protection": (
                "proxy: test file inventory cannot prove failure-before-pass replay"
            ),
            "requirements-adherence": "judge verdict unsatisfied: defect evidence missing",
        },
        judge_outputs={"requirements-adherence": {"verdict": "unsatisfied"}},
    )


def main() -> None:
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../experiments/benchmarks")
    dest.mkdir(parents=True, exist_ok=True)
    for experiment_dir in generate_synthetic_benchmark(dest):
        print(f"Wrote synthetic experiment: {experiment_dir}")


if __name__ == "__main__":
    main()
