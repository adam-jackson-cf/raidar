from types import SimpleNamespace

from raidar.schemas.scorecard import (
    CoverageScore,
    FunctionalScore,
    ResourceEfficiencyScore,
    VerificationStabilityScore,
    VisualScore,
)
from raidar.scorers.base import ScorerContext
from raidar.scorers.common import (
    artifact_metric_score,
    coverage_metric_score,
    coverage_output_metric_score,
    functional_metric_score,
    missing_required_artifacts,
    required_artifact_patterns,
    verification_stability_metric_score,
)
from raidar.scorers.design_to_code import DesignToCode, visual_regression_metric_score
from raidar.scorers.requirements import Requirements
from raidar.scorers.resource_efficiency import ResourceEfficiency


def _outputs(**overrides):
    values = {
        "functional": FunctionalScore(
            passed=True,
            tests_passed=2,
            tests_total=2,
            build_succeeded=True,
        ),
        "verification_stability": VerificationStabilityScore(total_gate_failures=1),
        "test_coverage": CoverageScore(threshold=0.8, measured=0.4, source="summary", passed=False),
        "visual": VisualScore(similarity=0.75, passed=True),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_core_metric_score_helpers_reflect_output_state(tmp_path):
    outputs = _outputs()

    assert functional_metric_score(outputs).evidence == "build=True, tests=2/2"
    assert verification_stability_metric_score(outputs).passed is True
    coverage_metric = coverage_output_metric_score(outputs)
    assert coverage_metric.score == 0.5
    assert coverage_metric.passed is False
    assert coverage_metric_score(CoverageScore(threshold=None, passed=True)) == 1.0
    assert coverage_metric_score(CoverageScore(threshold=None, passed=False)) == 0.0
    assert coverage_metric_score(CoverageScore(threshold=0.8, measured=None)) == 0.0

    assert visual_regression_metric_score(outputs).score == 0.75
    no_visual = visual_regression_metric_score(_outputs(visual=None))
    assert no_visual.passed is False
    assert no_visual.evidence == "Visual threshold not configured."


def test_artifact_pattern_collection_and_scoring(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "src" / "app").mkdir(parents=True)
    (workspace / "src" / "app" / "page.tsx").write_text("page", encoding="utf-8")
    scenario = SimpleNamespace(
        scorers=[
            SimpleNamespace(
                id="design-to-code",
                config={
                    "artifact-checks": {
                        "required_paths": ["src/app/page.tsx", "src/app/page.tsx", 42]
                    }
                },
            ),
            SimpleNamespace(id="other", config={"artifact-checks": {"required_paths": ["x"]}}),
        ]
    )

    patterns = required_artifact_patterns(scenario, "design-to-code")

    assert patterns == ("src/app/page.tsx",)
    assert missing_required_artifacts(workspace, patterns) == []
    metric = artifact_metric_score(workspace, ("src/app/page.tsx", "missing.txt"))
    assert metric.score == 0.5
    assert metric.matched_count == 1
    assert metric.missing_patterns == ["missing.txt"]
    assert artifact_metric_score(workspace, ()).score == 1.0


def test_registered_scorers_collect_expected_evidence(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scenario = SimpleNamespace(
        scorers=[
            SimpleNamespace(
                id="design-to-code",
                config={"artifact-checks": {"required_paths": []}},
            )
        ]
    )
    execution = SimpleNamespace(outputs=_outputs())
    context = SimpleNamespace(
        workspace=workspace,
        scenario_dir=tmp_path / "scenario",
        scenario=scenario,
        execution=execution,
        resource_efficiency=ResourceEfficiencyScore(uncached_input_tokens=10, command_count=2),
    )

    resource = ResourceEfficiency().collect_evidence(context)
    assert resource.metric_scores[0].metric_id == "resource-efficiency"
    assert "uncached_input_tokens=10" in resource.metric_scores[0].evidence

    design = DesignToCode().collect_evidence(context)
    assert [metric.metric_id for metric in design.metric_scores] == [
        "visual-regression",
        "functional",
        "test-coverage",
        "verification-stability",
        "artifact-checks",
    ]

    calls = []
    monkeypatch.setattr(
        "raidar.scorers.requirements.evaluate_llm_as_judge_metric",
        lambda **kwargs: calls.append(kwargs)
        or SimpleNamespace(metric_id=kwargs["metric_id"], score=1.0, passed=True),
    )
    requirements = Requirements().collect_evidence(context)
    assert [metric.metric_id for metric in requirements.metric_scores] == [
        "requirements-coverage",
        "requirements-adherence",
    ]
    assert calls[0]["judge_path"] == "judges/requirements-adherence.toml"


def test_scorer_context_type_import_is_available():
    assert ScorerContext is not None
