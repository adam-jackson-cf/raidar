import csv
import json
from datetime import UTC, datetime

from raidar.schemas.events import GateEvent
from raidar.schemas.scorecard import (
    EvalConfig,
    EvalRun,
    ExecutionValidityScore,
    FunctionalScore,
    GateCheck,
    MetricScore,
    PerformanceGatesScore,
    RequirementsCoverageScore,
    ResourceEfficiencyScore,
    Scorecard,
    ScorerResult,
    VerificationStabilityScore,
    VisualScore,
)
from raidar.storage import (
    _csv_row,
    _dict_meta,
    _failed_categories,
    _group_stats,
    _normalized_lower_better,
    _phase_timings,
    _safe_average,
    _safe_ratio,
    _variance,
    aggregate_results,
    export_to_csv,
    generate_comparison_report,
    load_all_runs,
    load_run,
    save_run,
)


def _run(
    run_id: str,
    *,
    unscored: bool = False,
    valid: bool = True,
    perf: bool = True,
    score: float = 0.8,
    duration: float = 10.0,
    visual: bool = True,
) -> EvalRun:
    scorecard = Scorecard(
        run_id=run_id,
        scenario_name="scenario",
        scenario_revision="v001",
        harness="codex-cli",
        model="openai/gpt",
        starter_root="starter",
        duration_sec=duration,
        unscored=unscored,
        unscored_reasons=["void"] if unscored else [],
        metadata={
            "run": {"run_json_path": f"/tmp/{run_id}/run.json"},
            "process": {
                "uncached_input_tokens": 100 if run_id == "run-a" else 300,
                "failed_command_categories": {"test": 1},
                "process_failed_command_count": 1,
                "first_pass_verification_successes": 2,
                "first_pass_verification_failures": 1,
                "missing_required_verification_commands": 0,
            },
            "harbor": {
                "phase_timings_sec": {
                    "trial_total_sec": 5.0,
                    "environment_setup_sec": 1.0,
                    "harness_setup_sec": 1.5,
                    "harness_execution_sec": 2.0,
                    "verifier_sec": 0.5,
                },
                "harness_overhead_sec": 0.4,
                "orchestration_overhead_excluding_test_sec": 0.2,
            },
        },
        functional=FunctionalScore(
            passed=valid,
            tests_passed=2,
            tests_total=2,
            build_succeeded=valid,
        ),
        visual=VisualScore(similarity=0.9) if visual else None,
        verification_stability=VerificationStabilityScore(
            total_gate_failures=0 if valid else 1,
            repeat_failures=0,
        ),
        requirements_coverage=RequirementsCoverageScore(
            total_requirements=2,
            satisfied_requirements=1 if valid else 0,
            mapped_requirements=1,
            requirement_gap_ids=["req"] if not valid else [],
            requirement_test_evidence_gaps={"req": ["test"]} if not valid else {},
        ),
        execution_validity=ExecutionValidityScore(checks=[GateCheck(name="valid", passed=valid)]),
        performance_gates=PerformanceGatesScore(checks=[GateCheck(name="perf", passed=perf)]),
        resource_efficiency=ResourceEfficiencyScore(
            uncached_input_tokens=100 if run_id == "run-a" else 300,
            command_count=2,
        ),
        metric_scores=[
            MetricScore(metric_id="artifact-checks", score=1.0, passed=True),
            MetricScore(metric_id="functional", score=1.0 if valid else 0.0, passed=valid),
        ],
        scorer_results=[
            ScorerResult(
                scorer_id="quality",
                version=1,
                category="quality",
                weight=1.0,
                score=score,
            )
        ],
    )
    return EvalRun(
        id=run_id,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        config=EvalConfig(
            model="openai/gpt",
            harness="codex-cli",
            scenario_name="scenario",
            scenario_revision="v001",
            starter_root="starter",
            evaluation_profile="profile",
            scorers=["quality"],
        ),
        duration_sec=duration,
        terminated_early=unscored,
        termination_reason="timeout" if unscored else None,
        scores=scorecard,
        gate_history=[
            GateEvent(
                timestamp="2026-01-01T00:00:00+00:00",
                gate_name="build",
                command="bun run build",
                exit_code=0,
                stdout="",
                stderr="",
            )
        ],
    )


def test_save_load_and_load_all_runs_skips_invalid_files(tmp_path):
    run = _run("run-a")

    path = save_run(run, tmp_path)

    assert path == tmp_path / "runs" / "run-a" / "run.json"
    assert load_run(path).id == "run-a"
    bad = tmp_path / "runs" / "bad" / "run.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{", encoding="utf-8")
    assert [loaded.id for loaded in load_all_runs(tmp_path)] == ["run-a"]


def test_aggregate_group_stats_and_safe_helpers():
    run_a = _run("run-a", score=0.8, duration=10)
    run_b = _run("run-b", score=0.4, duration=20, valid=False)
    run_c = _run("run-c", unscored=True)

    assert _variance([1.0]) == 0.0
    assert _variance([1.0, 3.0]) == 1.0
    assert _safe_ratio(1, 0) == 0.0
    assert _safe_average([]) == 0.0
    assert _dict_meta("bad") == {}
    assert _group_stats([])["count"] == 0

    stats = _group_stats([run_a, run_b, run_c])
    assert stats["count"] == 3
    assert stats["scored_count"] == 2
    assert stats["unscored_count"] == 1
    assert stats["validity_rate"] == 0.5
    assert stats["avg_duration_sec"] == 15

    aggregate = aggregate_results([run_a, run_b, run_c])
    assert aggregate["total_runs"] == 3
    assert "codex-cli" in aggregate["by_harness"]
    assert aggregate_results([]) == {"total_runs": 0}


def test_csv_export_row_contains_process_phase_and_artifact_metrics(tmp_path):
    run = _run("run-a")
    output = tmp_path / "runs.csv"

    export_to_csv([run], output)
    export_to_csv([], tmp_path / "empty.csv")

    rows = list(csv.DictReader(output.open()))
    assert rows[0]["run_id"] == "run-a"
    assert rows[0]["artifact_checks_passed"] == "True"
    assert rows[0]["trial_total_sec"] == "5.0"
    row = _csv_row(run)
    assert json.loads(row["metric_scores"]) == ["artifact-checks", "functional"]
    assert json.loads(row["failed_command_categories"]) == {"test": 1}
    assert _phase_timings(run)["verifier_sec"] == 0.5

    no_meta = _run("run-no-meta")
    no_meta.scores.metadata["process"] = "bad"
    no_meta.scores.metadata["harbor"] = "bad"
    no_meta.scores.metric_scores = []
    row = _csv_row(no_meta)
    assert row["artifact_checks_passed"] is None
    assert row["visual_similarity"] == 0.9


def test_csv_export_redacts_unscored_reason_values(tmp_path):
    run = _run("run-secret", unscored=True)
    run.scores.unscored_reasons = ["DB_PASSWORD=abcdefghijklmnop"]
    output = tmp_path / "runs.csv"

    export_to_csv([run], output)

    payload = output.read_text(encoding="utf-8")
    assert "abcdefghijklmnop" not in payload
    assert "DB_PASSWORD=<redacted>" in payload


def test_comparison_report_sections_cover_valid_invalid_and_unscored_runs(monkeypatch):
    monkeypatch.setattr(
        "raidar.storage.datetime", type("D", (), {"now": staticmethod(datetime.now)})
    )
    valid = _run("run-a", duration=10)
    invalid = _run("run-b", valid=False, duration=20, visual=False)
    unscored = _run("run-c", unscored=True, duration=30)
    unscored.scores.unscored_reasons = ["OPENAI_API_KEY=abcdefghijklmnop"]
    unscored.termination_reason = "Bearer abcdefghijklmnop password=hunter2value"

    assert _normalized_lower_better(1, 1, 1) == 1.0
    assert _normalized_lower_better(5, 0, 10) == 0.5
    assert _failed_categories(valid) == {"test": 1}
    valid.scores.metadata["process"] = {"failed_command_categories": "bad"}
    assert _failed_categories(valid) == {}
    valid.scores.metadata["process"] = {"failed_command_categories": {"test": 1}}

    report = generate_comparison_report([valid, invalid, unscored])

    assert "# Experiment Comparison Report" in report
    assert "## Summary Table" in report
    assert "## Valid Run Cost-Time Index" in report
    assert "## Diagnostic Ranking (Invalid Runs)" in report
    assert "## Unscored Runs (Rerun Required)" in report
    assert "run_id=run-c" in report
    assert "abcdefghijklmnop" not in report
    assert "hunter2value" not in report
    assert "OPENAI_API_KEY=<redacted>" in report
    assert generate_comparison_report([]) == "# Experiment Report\n\nNo runs to report."

    all_unscored_report = generate_comparison_report([unscored])
    assert "- No valid runs; cost/time normalization skipped." in all_unscored_report
    assert "- No invalid runs." in generate_comparison_report([valid])
    assert "- No unscored runs." in generate_comparison_report([valid])
