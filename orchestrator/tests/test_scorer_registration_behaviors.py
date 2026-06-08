from types import SimpleNamespace

import pytest

from raidar.schemas.scenario import DeterministicCheck
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
    code_task_artifact_metric_score,
    functional_metric_score,
    missing_required_artifacts,
    required_artifact_patterns,
    verification_stability_metric_score,
)
from raidar.scorers.design_to_code import (
    DesignToCode,
    design_to_code_coverage_metric_score,
    visual_regression_metric_score,
)
from raidar.scorers.deterministic import (
    check_file_exists,
    check_import_present,
    check_no_pattern,
    parse_judge_response,
    run_deterministic_check,
    score_requirement_checks,
    validate_safe_regex_pattern,
)
from raidar.scorers.registry import (
    ScorerResolutionError,
    _metric_definition,
    load_scorer_definition,
    resolve_scorers,
    resolved_metrics,
    scenario_scorers,
    scorer_evaluation_profile,
)
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
    coverage_metric = design_to_code_coverage_metric_score(outputs)
    assert coverage_metric.score == 0.5
    assert coverage_metric.passed is False
    assert (
        design_to_code_coverage_metric_score(
            _outputs(test_coverage=CoverageScore(threshold=None, passed=True))
        ).score
        == 1.0
    )
    assert (
        design_to_code_coverage_metric_score(
            _outputs(test_coverage=CoverageScore(threshold=None, passed=False))
        ).score
        == 0.0
    )
    assert (
        design_to_code_coverage_metric_score(
            _outputs(test_coverage=CoverageScore(threshold=0.8, measured=None))
        ).score
        == 0.0
    )

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

    code_task_metric = code_task_artifact_metric_score(
        language_label="typescript",
        files=[workspace / "src" / "app" / "page.tsx"],
        tests=[],
        workspace=workspace,
        required_artifacts=("src/app/page.tsx", "missing.txt"),
        is_test_file=lambda _path, _workspace: False,
    )
    assert code_task_metric.score == 0.25
    assert code_task_metric.missing_patterns == ["typescript test files", "missing.txt"]
    assert code_task_metric.evidence == "source_files=1, test_files=0"


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


def test_deterministic_helpers_cover_pass_fail_and_safety_paths(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "page.tsx").write_text("import Ready from './ready';\nconst token = 'bad';\n")

    assert parse_judge_response("VERDICT: PASS\nEVIDENCE: ok").passed is True
    assert parse_judge_response("FAIL: missing requirement").passed is False
    assert parse_judge_response("unclear").evidence.startswith("Could not parse response")

    assert check_import_present(tmp_path, "Ready")[0] is True
    assert check_import_present(tmp_path / "missing", "Ready")[1] == "src directory not found"
    assert check_file_exists(tmp_path, "src/*.tsx")[0] is True
    assert check_file_exists(tmp_path, "src/*.ts")[0] is False
    assert validate_safe_regex_pattern("(a+)+$")[0] is False
    assert validate_safe_regex_pattern("(a|aa)+$")[0] is False
    assert validate_safe_regex_pattern("a" * 513)[0] is False
    assert check_no_pattern(tmp_path, "token")[0] is False
    assert check_no_pattern(tmp_path, "absent")[0] is True
    assert check_no_pattern(tmp_path, "(a+)+$")[0] is False
    assert check_no_pattern(tmp_path / "missing", "token")[0] is True

    check = DeterministicCheck(
        type="file_exists",
        pattern="src/page.tsx",
        description="page exists",
    )
    result = run_deterministic_check(check, tmp_path)
    assert result.passed is True
    assert score_requirement_checks([result]) == 1.0
    assert score_requirement_checks([]) == 1.0

    no_pattern = DeterministicCheck(
        type="no_pattern",
        pattern="absent",
        description="absent token stays absent",
    )
    assert run_deterministic_check(no_pattern, tmp_path).passed is True
    unknown = DeterministicCheck.model_construct(
        type="unknown",
        pattern="anything",
        description="unknown checks fail closed",
    )
    assert run_deterministic_check(unknown, tmp_path).evidence == "Unknown check type: unknown"


def test_requirements_coverage_scores_empty_and_missing_requirements(tmp_path):
    empty_context = SimpleNamespace(
        workspace=tmp_path,
        scenario_dir=tmp_path,
        scenario=SimpleNamespace(requirements=SimpleNamespace(items=[])),
        execution=SimpleNamespace(outputs=_outputs()),
        resource_efficiency=SimpleNamespace(),
        execution_validity=SimpleNamespace(),
    )
    calls = []

    def fake_judge(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(metric_id=kwargs["metric_id"], score=1.0, passed=True)

    import raidar.scorers.requirements as requirements_module

    original_judge = requirements_module.evaluate_llm_as_judge_metric
    requirements_module.evaluate_llm_as_judge_metric = fake_judge
    try:
        empty_scores = Requirements().collect_evidence(empty_context).metric_scores
        assert empty_scores[0].score == 1.0
        assert empty_scores[0].evidence == "requirements=0"

        missing_context = SimpleNamespace(
            **{
                **empty_context.__dict__,
                "scenario": SimpleNamespace(
                    requirements=SimpleNamespace(
                        items=[
                            SimpleNamespace(
                                id="req-a",
                                check=DeterministicCheck(
                                    type="import_present",
                                    pattern="MissingSymbol",
                                    description="Missing symbol is imported",
                                ),
                            )
                        ]
                    )
                ),
            }
        )
        missing_scores = Requirements().collect_evidence(missing_context).metric_scores
    finally:
        requirements_module.evaluate_llm_as_judge_metric = original_judge

    assert missing_scores[0].score == 0.0
    assert missing_scores[0].missing_patterns == ["req-a"]
    assert calls[-1]["metric_id"] == "requirements-adherence"


def test_scorer_registry_resolution_and_metric_definition_edges():
    scenario = SimpleNamespace(
        scorers=[
            SimpleNamespace(id="typescript-code-task", version=1, weight=0.8, config={}),
            SimpleNamespace(id="resource-efficiency", version=1, weight=0.2, config={}),
        ]
    )

    resolved = resolve_scorers(scenario)

    assert resolved[0].ref == "typescript-code-task@1"
    assert scenario_scorers(scenario) == ["typescript-code-task@1", "resource-efficiency@1"]
    assert scorer_evaluation_profile(scenario) == (
        "scorers:typescript-code-task@1:0.8+resource-efficiency@1:0.2"
    )
    assert {metric.id for metric in resolved_metrics(scenario)} >= {
        "functional",
        "code-quality",
        "artifact-checks",
    }
    judged_metrics = resolved_metrics(
        SimpleNamespace(
            scorers=[SimpleNamespace(id="requirements", version=1, weight=1.0, config={})]
        )
    )
    assert [metric.id for metric in judged_metrics] == [
        "requirements-coverage",
        "requirements-adherence",
    ]

    zero_weight = SimpleNamespace(
        scorers=[SimpleNamespace(id="resource-efficiency", version=1, weight=0.0, config={})]
    )
    with pytest.raises(ScorerResolutionError, match="positive total"):
        resolve_scorers(zero_weight)

    bad_metric = SimpleNamespace(id="custom", type="unsupported", config={})
    with pytest.raises(ScorerResolutionError, match="Unsupported scorer metric type"):
        _metric_definition(bad_metric)


def test_removed_generic_and_acceptance_scorers_are_unknown():
    for scorer_id in ("code-task", "acceptance"):
        with pytest.raises(
            ScorerResolutionError,
            match=f"Unknown scorer definition: {scorer_id}@1",
        ):
            load_scorer_definition(scorer_id, 1)
