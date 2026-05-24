import json
from datetime import UTC, datetime
from types import SimpleNamespace

from raidar.runtime import artifacts
from raidar.runtime.models import (
    EvaluationOutputs,
    HarborExecutionResult,
    RunLayout,
)
from raidar.schemas.events import GateEvent
from raidar.schemas.scorecard import (
    AcceptanceScore,
    CoverageScore,
    ExecutionValidityScore,
    FunctionalScore,
    GateCheck,
    MetricScore,
    PerformanceGatesScore,
    RequirementsCoverageScore,
    Scorecard,
    ScorerResult,
    VerificationStabilityScore,
    VisualScore,
)


def _outputs(visual: VisualScore | None = None) -> EvaluationOutputs:
    return EvaluationOutputs(
        functional=FunctionalScore(
            passed=True,
            tests_passed=1,
            tests_total=1,
            build_succeeded=True,
            gates_passed=1,
            gates_total=1,
        ),
        acceptance=AcceptanceScore(),
        visual=visual,
        verification_stability=VerificationStabilityScore(),
        test_coverage=CoverageScore(threshold=0.8, measured=0.9, source="summary", passed=True),
        requirements_coverage=RequirementsCoverageScore(),
        execution_validity=ExecutionValidityScore(
            checks=[GateCheck(name="valid", passed=True, evidence="ok")]
        ),
        performance_gates=PerformanceGatesScore(
            checks=[GateCheck(name="perf", passed=True, evidence="ok")]
        ),
        metric_scores=[MetricScore(metric_id="functional", score=1.0, passed=True)],
        gate_history=[
            GateEvent(
                timestamp="2026-01-01T00:00:00+00:00",
                gate_name="build",
                command="bun run build",
                exit_code=0,
                stdout="ok",
                stderr="",
            )
        ],
    )


def test_load_verifier_outputs_reports_missing_invalid_and_valid_scorecards(monkeypatch, tmp_path):
    monkeypatch.setattr(artifacts, "_verifier_scorecard_path", lambda trial: None)
    assert artifacts._load_verifier_outputs(tmp_path) == (
        None,
        "Harbor trial directory not found.",
    )

    scorecard_path = tmp_path / "scorecard.json"
    monkeypatch.setattr(artifacts, "_verifier_scorecard_path", lambda trial: scorecard_path)
    assert "Verifier scorecard missing" in artifacts._load_verifier_outputs(tmp_path)[1]

    scorecard_path.write_text("{", encoding="utf-8")
    assert (
        artifacts._load_verifier_outputs(tmp_path)[1]
        == "Invalid verifier scorecard JSON: Expecting property name enclosed in double quotes"
    )

    scorecard_path.write_text("[]", encoding="utf-8")
    assert artifacts._load_verifier_outputs(tmp_path)[1] == (
        "Invalid verifier scorecard content: expected object root."
    )

    scorecard_path.write_text('{"gate_history":[]}', encoding="utf-8")
    assert "Invalid verifier scorecard content" in artifacts._load_verifier_outputs(tmp_path)[1]

    payload = {
        "functional": _outputs().functional.model_dump(mode="json"),
        "acceptance": _outputs().acceptance.model_dump(mode="json"),
        "visual": None,
        "verification_stability": _outputs().verification_stability.model_dump(mode="json"),
        "test_coverage": _outputs().test_coverage.model_dump(mode="json"),
        "requirements_coverage": _outputs().requirements_coverage.model_dump(mode="json"),
        "execution_validity": _outputs().execution_validity.model_dump(mode="json"),
        "performance_gates": _outputs().performance_gates.model_dump(mode="json"),
        "metric_scores": [metric.model_dump(mode="json") for metric in _outputs().metric_scores],
        "gate_history": [event.model_dump(mode="json") for event in _outputs().gate_history],
    }
    scorecard_path.write_text(json.dumps(payload), encoding="utf-8")
    loaded, error = artifacts._load_verifier_outputs(tmp_path)
    assert error is None
    assert loaded.functional.passed is True
    assert loaded.visual is None


def test_persist_verifier_and_canonical_verifier_artifacts(tmp_path):
    trial_dir = tmp_path / "trial"
    verifier_source = trial_dir / "verifier"
    verifier_source.mkdir(parents=True)
    for name in ("scorecard.json", "reward.txt", "test-stdout.txt"):
        (verifier_source / name).write_text(name, encoding="utf-8")

    harbor_result = HarborExecutionResult(False, None, tmp_path / "job", trial_dir)
    target = tmp_path / "canonical" / "verifier"
    target.mkdir(parents=True)

    copied = artifacts.persist_verifier_artifacts(harbor_result, target)
    assert set(copied) == {"scorecard.json", "reward.txt", "test-stdout.txt"}
    assert (
        artifacts.persist_verifier_artifacts(
            HarborExecutionResult(False, None, tmp_path / "job", None), target
        )
        == {}
    )
    assert (
        artifacts.persist_verifier_artifacts(
            HarborExecutionResult(False, None, tmp_path / "job", tmp_path / "missing"), target
        )
        == {}
    )

    layout = RunLayout(
        run_id="run",
        start_time=datetime.now(UTC),
        run_label="label",
        root_dir=tmp_path / "run",
        workspace_dir=tmp_path / "workspace",
        verifier_dir=target,
        harness_dir=tmp_path / "harness",
        harbor_dir=tmp_path / "harbor",
        run_json_path=tmp_path / "run.json",
        report_path=tmp_path / "report.md",
    )
    scorecard = Scorecard(
        execution_validity=ExecutionValidityScore(checks=[GateCheck(name="valid", passed=False)]),
        performance_gates=PerformanceGatesScore(checks=[GateCheck(name="perf", passed=True)]),
    )
    artifacts.persist_canonical_verifier_artifacts(layout, scorecard, _outputs())

    assert "gate_history" in (target / "scorecard.json").read_text(encoding="utf-8")
    assert (target / "reward.txt").read_text(encoding="utf-8") == "0"


def test_visual_evidence_persistence_and_rebinding(monkeypatch, tmp_path):
    scenario_dir = tmp_path / "scenario"
    reference_dir = scenario_dir / "reference"
    reference_dir.mkdir(parents=True)
    for name in ("page.png", "page-region-hero.png"):
        (reference_dir / name).write_text(name, encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("actual.png", "diff.png", "actual-region-hero.png", "diff-region-hero.png"):
        (workspace / name).write_text(name, encoding="utf-8")

    scenario = SimpleNamespace(
        visual=SimpleNamespace(reference_image="reference/page.png", regions=[]),
    )
    run_request = SimpleNamespace(scenario=scenario, scenario_dir=scenario_dir)
    request = artifacts.VisualEvidenceRequest(
        request=run_request,
        workspace=workspace,
        run_root_dir=tmp_path / "run",
    )

    evidence = artifacts._persist_visual_evidence_artifacts(request)

    assert evidence["actual"].endswith("visual/actual.png")
    assert evidence["reference"].endswith("visual/page.png")
    assert evidence["diff"].endswith("visual/diff.png")
    assert evidence["regions"][0]["name"] == "hero"

    visual = VisualScore(regional_scores=[{"name": "hero"}, {"name": "missing"}])
    visual.regional_scores.append("ignored")
    artifacts._rebind_visual_evidence_paths(visual, evidence)
    assert visual.actual_path == evidence["actual"]
    assert visual.regional_scores[0]["actual_path"].endswith("actual-region-hero.png")
    assert "actual_path" not in visual.regional_scores[1]
    artifacts._rebind_visual_evidence_paths(None, evidence)

    no_visual_request = artifacts.VisualEvidenceRequest(
        request=SimpleNamespace(scenario=SimpleNamespace(visual=None)),
        workspace=workspace,
        run_root_dir=tmp_path / "run-no-visual",
    )
    assert artifacts._persist_visual_evidence_artifacts(no_visual_request) == {
        "actual": None,
        "reference": None,
        "diff": None,
        "regions": [],
    }


def test_harness_and_harbor_artifact_persistence(tmp_path):
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True)
    for name in ("trajectory.json", "codex.txt", "final-app.tar.gz"):
        (agent_dir / name).write_text(name, encoding="utf-8")
    setup_dir = agent_dir / "setup"
    setup_dir.mkdir()
    (setup_dir / "install.log").write_text("setup", encoding="utf-8")
    command_dir = agent_dir / "command-001"
    command_dir.mkdir()
    (command_dir / "stdout.txt").write_text("out", encoding="utf-8")
    (agent_dir / "command-file").write_text("skip", encoding="utf-8")

    harbor_result = HarborExecutionResult(False, None, tmp_path / "job", trial_dir)
    harness_dir = tmp_path / "harness"
    harness_dir.mkdir()
    copied = artifacts.persist_harness_artifacts(harbor_result, harness_dir)

    assert copied["trajectory.json"].endswith("trajectory.json")
    assert copied["project.final.tar.gz"].endswith("project.final.tar.gz")
    assert copied["setup"].endswith("setup")
    assert copied["commands/command-001"].endswith("command-001")
    assert (
        artifacts.persist_harness_artifacts(
            HarborExecutionResult(False, None, tmp_path / "job", None), harness_dir
        )
        == {}
    )
    assert (
        artifacts.persist_harness_artifacts(
            HarborExecutionResult(False, None, tmp_path / "job", tmp_path / "missing"), harness_dir
        )
        == {}
    )

    harbor_dir = tmp_path / "harbor"
    harbor_dir.mkdir()
    (harbor_dir / "command.txt").write_text("harbor run", encoding="utf-8")
    harbor_artifacts = artifacts.persist_harbor_artifacts(harbor_result, harbor_dir)
    assert harbor_artifacts["command.txt"].endswith("command.txt")
    assert harbor_artifacts["raw_job_dir"] == str(tmp_path / "job")
    assert harbor_artifacts["raw_trial_dir"] == str(trial_dir)


def test_write_run_analysis_records_score_and_artifact_pointers(monkeypatch, tmp_path):
    layout = RunLayout(
        run_id="run-1",
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        run_label="label",
        root_dir=tmp_path / "run",
        workspace_dir=tmp_path / "workspace",
        verifier_dir=tmp_path / "verifier",
        harness_dir=tmp_path / "harness",
        harbor_dir=tmp_path / "harbor",
        run_json_path=tmp_path / "run.json",
        report_path=tmp_path / "report.md",
    )
    request = SimpleNamespace(
        scenario=SimpleNamespace(name="scenario"),
        config=SimpleNamespace(
            harness=SimpleNamespace(value="codex-cli"),
            model=SimpleNamespace(qualified_name="openai/gpt-5.5"),
        ),
    )
    scorecard = Scorecard(
        metadata={
            "evidence": {
                "homepage_post": "post.png",
                "final_workspace_archive": "final.tar.gz",
                "errors": ["none"],
            },
            "workspace": {
                "prune": {"removed": ["node_modules"], "reclaimed_bytes": 100},
                "changes": {
                    "changed_file_count": 2,
                    "changed_files": ["a", "b"],
                    "artifact": "workspace-diff.json",
                    "error": None,
                },
            },
        },
        execution_validity=ExecutionValidityScore(checks=[GateCheck(name="valid", passed=True)]),
        performance_gates=PerformanceGatesScore(checks=[GateCheck(name="perf", passed=True)]),
        scorer_results=[
            ScorerResult(scorer_id="quality", version=1, category="quality", weight=1, score=0.8)
        ],
    )
    monkeypatch.setattr(artifacts, "_harness_event_stream_pointer", lambda _dir, _harness: "stream")

    artifacts.write_run_analysis(
        layout,
        request,
        scorecard,
        HarborExecutionResult(False, None, tmp_path / "job", tmp_path / "trial"),
    )

    text = layout.report_path.read_text(encoding="utf-8")
    assert "- run_id: `run-1`" in text
    assert "- quality_score: `0.800000`" in text
    assert "- harness_event_stream: `stream`" in text
    assert "- workspace_changed_file_count: `2`" in text
