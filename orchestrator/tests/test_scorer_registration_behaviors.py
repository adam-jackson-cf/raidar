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
    _action_exit_code,
    _pct_number,
    _visual_artifact_paths,
    _visual_score_from_result,
    design_functional_metric_score,
    design_to_code_coverage_metric_score,
    design_verification_stability_metric_score,
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
from raidar.scorers.verification import VerificationEvidence


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


def _visual_scoring_config() -> SimpleNamespace:
    return SimpleNamespace(
        region_pass_threshold=0.9,
        gamma=1.0,
        bands=SimpleNamespace(
            global_band=SimpleNamespace(lower=0.8, upper=1.0),
            regional=SimpleNamespace(lower=0.8, upper=1.0),
            worst_region=SimpleNamespace(lower=0.75, upper=1.0),
        ),
        weights=SimpleNamespace(
            global_weight=0.4,
            regional=0.25,
            worst_region=0.25,
            region_pass_rate=0.1,
        ),
    )


def _visual_pass_policy() -> SimpleNamespace:
    return SimpleNamespace(
        minimum_score=80.0,
        minimum_region_pass_rate=1.0,
        minimum_worst_region=0.9,
        fail_if_global_below=0.7,
        fail_if_worst_region_below=0.65,
        high_fidelity_score=85.0,
        high_fidelity_global=0.95,
        high_fidelity_worst_region=0.95,
    )


def _visual_config() -> SimpleNamespace:
    return SimpleNamespace(
        scoring=_visual_scoring_config(),
        pass_policy=_visual_pass_policy(),
    )


def _design_context(tmp_path, actions, *, visual=None, coverage_threshold=0.8):
    workspace = tmp_path / "workspace"
    (workspace / "src" / "app").mkdir(parents=True)
    (workspace / "src" / "app" / "page.tsx").write_text("export default function Page() {}\n")
    scenario = SimpleNamespace(
        visual=visual,
        verification=SimpleNamespace(coverage_threshold=coverage_threshold),
        scorers=[
            SimpleNamespace(
                id="design-to-code",
                config={"artifact-checks": {"required_paths": ["src/app/page.tsx"]}},
            )
        ],
    )
    return SimpleNamespace(
        workspace=workspace,
        scenario_dir=tmp_path / "scenario",
        scenario=scenario,
        execution=SimpleNamespace(outputs=_outputs()),
        resource_efficiency=SimpleNamespace(),
        execution_validity=SimpleNamespace(),
        verification_evidence=VerificationEvidence(actions=actions),
    )


def _command_action(exit_code):
    return {"kind": "command", "result": {"exit_code": exit_code}}


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


def test_design_to_code_scores_visual_diff_evidence_with_regions(tmp_path):
    actions = {
        "design-to-code.typecheck": _command_action(0),
        "design-to-code.lint": _command_action(0),
        "design-to-code.coverage": _command_action(0),
        "design-to-code.build": _command_action(0),
        "design-to-code.coverage.summary": {
            "kind": "read_json",
            "result": {"content": {"total": {"lines": {"pct": 92.0}}}},
        },
        "design-to-code.visual.homepage": {
            "kind": "visual_diff",
            "result": {
                "global": {"similarity": 0.98},
                "expected_region_count": 2,
                "regions": [
                    {
                        "name": "hero",
                        "weight": 2,
                        "normalized_weight": 0.75,
                        "comparison": {"similarity": 0.97},
                    },
                    {
                        "name": "cta",
                        "weight": 1,
                        "normalized_weight": 0.25,
                        "comparison": {"similarity": 0.95},
                    },
                ],
            },
        },
    }
    context = _design_context(tmp_path, actions, visual=_visual_config())

    evidence = DesignToCode().collect_evidence(context)
    metrics = {metric.metric_id: metric for metric in evidence.metric_scores}

    assert metrics["visual-regression"].passed is True
    assert metrics["visual-regression"].score > 0.85
    assert "tier=high_fidelity" in metrics["visual-regression"].evidence
    assert metrics["functional"].passed is True
    assert metrics["test-coverage"].score == 1.0
    assert metrics["test-coverage"].passed is True
    assert metrics["verification-stability"].passed is True
    assert metrics["artifact-checks"].matched_count == 1


def test_design_to_code_visual_policy_fails_low_global_and_missing_regions(tmp_path):
    context = _design_context(
        tmp_path,
        {
            "design-to-code.build": _command_action(0),
            "design-to-code.visual.homepage": {
                "kind": "visual_diff",
                "result": {
                    "global": {"similarity": 0.69},
                    "expected_region_count": 1,
                    "regions": [
                        {
                            "name": "hero",
                            "comparison": {"similarity": 0.96},
                        },
                        "ignored",
                    ],
                },
            },
        },
        visual=_visual_config(),
    )

    visual_metric = visual_regression_metric_score(context)

    assert visual_metric.passed is False
    assert visual_metric.score < 0.8
    assert "tier=failed" in visual_metric.evidence


def test_design_to_code_handles_missing_visual_evidence_and_fallback_paths(tmp_path):
    visual = _visual_config()
    context = _design_context(
        tmp_path,
        {
            "design-to-code.build": {"kind": "command", "result": {}},
            "design-to-code.coverage.summary": {"kind": "read_json", "result": {"content": {}}},
        },
        visual=visual,
    )

    visual_metric = visual_regression_metric_score(context)
    coverage_metric = design_to_code_coverage_metric_score(context)
    score = _visual_score_from_result({"global": {"similarity": 0.93}}, visual)

    assert visual_metric.evidence == "Visual comparison evidence missing."
    assert visual_metric.passed is False
    assert coverage_metric.score == 0.0
    assert coverage_metric.passed is False
    assert score["regional_similarity"] == 0.93
    assert score["worst_region_similarity"] == 0.93
    assert score["fidelity_tier"] == "failed"
    assert _visual_artifact_paths(SimpleNamespace(artifacts=[])) == {
        "actual": "artifacts/actual.png",
        "diff": "artifacts/diff.png",
    }
    assert _visual_artifact_paths(
        SimpleNamespace(
            artifact_manifest=SimpleNamespace(
                actual_image="screens/actual.png",
                diff_image="screens/diff.png",
            )
        )
    ) == {"actual": "screens/actual.png", "diff": "screens/diff.png"}
    fallback_metric = visual_regression_metric_score(
        SimpleNamespace(
            scenario=SimpleNamespace(visual=None), execution=SimpleNamespace(outputs=_outputs())
        )
    )
    assert fallback_metric.score == 0.75
    assert _action_exit_code({"result": "not-a-dict"}) is None
    assert _pct_number("nope") is None


def test_design_to_code_evidence_helpers_handle_failures_and_coverage_shapes(tmp_path):
    actions = {
        "design-to-code.typecheck": _command_action(0),
        "design-to-code.lint": _command_action(1),
        "design-to-code.build": _command_action(0),
        "design-to-code.coverage.summary": {
            "kind": "read_json",
            "result": {"content": {"totals": {"percent_covered": 64.0}}},
        },
    }
    context = _design_context(tmp_path, actions, visual=None, coverage_threshold=0.8)

    functional = design_functional_metric_score(context)
    coverage = design_to_code_coverage_metric_score(context)
    stability = design_verification_stability_metric_score(context)

    assert functional.passed is False
    assert functional.evidence == "build=True, commands=2/3"
    assert coverage.score == 0.8
    assert coverage.passed is False
    assert stability.score < 1.0
    assert stability.passed is False

    missing_coverage_context = _design_context(
        tmp_path / "missing",
        {"design-to-code.build": {"kind": "command", "result": {}}},
        visual=None,
        coverage_threshold=0.0,
    )
    assert design_to_code_coverage_metric_score(missing_coverage_context).score == 1.0
    assert design_verification_stability_metric_score(missing_coverage_context).passed is True


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
