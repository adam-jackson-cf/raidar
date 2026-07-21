import json

from types import SimpleNamespace

import pytest

from raidar.schemas.scenario import DeterministicCheck
from raidar.schemas.scorecard import (
    ResourceEfficiencyScore,
)
from raidar.runtime.scoring_outputs import (
    _load_baseline_verification_evidence,
    _load_verification_evidence,
)
from raidar.scorers.base import ScorerContext
from raidar.scorers.common import (
    artifact_metric_score,
    code_task_artifact_metric_score,
    functional_score_from_evidence,
    missing_required_artifacts,
    required_artifact_patterns,
    verification_stability_from_evidence,
)
from raidar.scorers.design_to_code import (
    DesignToCode,
    _pct_number,
    _visual_artifact_paths,
    _visual_score_from_result,
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
from raidar.scorers.evidence_contract import (
    CommandEvidenceResult,
    EvidenceActionError,
    EvidenceActionResult,
    JsonEvidenceResult,
    VisualComparisonEvidence,
    VisualDiffEvidenceResult,
    VisualPathEvidence,
    VisualRegionEvidence,
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
from raidar.scorers.verification import VerificationEvidence, VerificationEvidenceError


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
        execution=SimpleNamespace(),
        resource_efficiency=SimpleNamespace(),
        execution_validity=SimpleNamespace(),
        verification_evidence=_typed_evidence(actions),
    )


def _command_action(exit_code):
    return {"kind": "command", "result": {"exit_code": exit_code}}


def _typed_evidence(actions: dict) -> VerificationEvidence:
    typed = {action_id: _typed_action(action_id, raw) for action_id, raw in actions.items()}
    return VerificationEvidence(
        actions=typed,
        aliases={action_id: action_id for action_id in typed},
    )


def _typed_action(action_id: str, raw: dict) -> EvidenceActionResult:
    kind = raw["kind"]
    result = raw.get("result")
    if not isinstance(result, dict) or not result:
        return EvidenceActionResult(
            id=action_id,
            kind=kind,
            required=action_id.endswith(".build"),
            status="error",
            duration_sec=0.1,
            error=EvidenceActionError(message="missing result", type="MissingEvidence"),
        )
    if kind == "command":
        command_result = CommandEvidenceResult(
            command=["bun", "run", action_id.rsplit(".", 1)[-1]],
            cwd=".",
            duration_sec=0.1,
            exit_code=int(result.get("exit_code", 0)),
            stdout=str(result.get("stdout", "")),
            stderr=str(result.get("stderr", "")),
            timed_out=False,
        )
        typed_result = command_result
    elif kind == "read_json":
        typed_result = JsonEvidenceResult(
            bytes=10,
            content=result.get("content", {}),
            path="coverage/coverage-summary.json",
            sha256="0" * 64,
            truncated=False,
        )
    else:
        typed_result = _typed_visual_result(result)
    return EvidenceActionResult(
        id=action_id,
        kind=kind,
        required=action_id.endswith(".build"),
        status="ok",
        duration_sec=0.1,
        result=typed_result,
    )


def _typed_visual_result(result: dict) -> VisualDiffEvidenceResult:
    def comparison(payload: object, diff_path: str):
        if not isinstance(payload, dict):
            return None
        return VisualComparisonEvidence(
            command=["custom-visual-tool"],
            exit_code=0,
            similarity=float(payload.get("similarity", 0)),
            raw_output="",
            diff_path=diff_path,
        )

    regions = []
    for raw_region in result.get("regions", []):
        if not isinstance(raw_region, dict):
            continue
        name = str(raw_region.get("name", ""))
        regions.append(
            VisualRegionEvidence(
                name=name,
                weight=float(raw_region.get("weight", 1)),
                normalized_weight=float(raw_region.get("normalized_weight", 1)),
                actual=VisualPathEvidence(exists=True, path=f"{name}-actual.png"),
                reference=VisualPathEvidence(exists=True, path=f"{name}-reference.png"),
                diff=VisualPathEvidence(exists=True, path=f"{name}-diff.png"),
                comparison=comparison(raw_region.get("comparison"), f"{name}-diff.png"),
            )
        )
    return VisualDiffEvidenceResult.model_validate(
        {
            "actual": {"exists": True, "path": "actual.png"},
            "reference": {"exists": True, "path": "reference.png"},
            "diff": {"exists": True, "path": "diff.png"},
            "threshold": 0.03,
            "capture_succeeded": True,
            "global": (
                comparison(result.get("global"), "diff.png").model_dump(mode="json")
                if comparison(result.get("global"), "diff.png")
                else None
            ),
            "regions": [region.model_dump(mode="json") for region in regions],
            "expected_region_count": int(result.get("expected_region_count", 0)),
            "available_region_count": len(regions),
        }
    )


def test_core_metric_score_helpers_reflect_output_state(tmp_path):
    context = _design_context(
        tmp_path,
        {
            "design-to-code.build": _command_action(0),
            "design-to-code.lint": _command_action(1),
        },
    )
    evidence = context.verification_evidence.for_owner("design-to-code")

    assert functional_score_from_evidence(evidence).passed is True
    assert verification_stability_from_evidence(evidence).passed is False
    no_visual = DesignToCode().collect_evidence(context).metric_scores[0]
    assert no_visual.passed is False
    assert no_visual.evidence == "Visual threshold not configured."


def test_artifact_pattern_collection_and_scoring(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "src" / "app").mkdir(parents=True)
    (workspace / "src" / "app" / "page.tsx").write_text("page", encoding="utf-8")
    scenario = SimpleNamespace(
        visual=None,
        verification=SimpleNamespace(coverage_threshold=None),
        requirements=SimpleNamespace(items=[]),
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
        ],
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
        visual=None,
        verification=SimpleNamespace(coverage_threshold=None),
        requirements=SimpleNamespace(items=[]),
        scorers=[
            SimpleNamespace(
                id="design-to-code",
                config={"artifact-checks": {"required_paths": []}},
            )
        ],
    )
    execution = SimpleNamespace()
    context = SimpleNamespace(
        workspace=workspace,
        scenario_dir=tmp_path / "scenario",
        scenario=scenario,
        execution=execution,
        resource_efficiency=ResourceEfficiencyScore(uncached_input_tokens=10, command_count=2),
        verification_evidence=VerificationEvidence(),
        workspace_changes={},
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

    visual_metric = visual_regression_metric_score(
        context, context.verification_evidence.for_owner("design-to-code")
    )

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

    metrics = {
        metric.metric_id: metric
        for metric in DesignToCode().collect_evidence(context).metric_scores
    }
    visual_metric = metrics["visual-regression"]
    coverage_metric = metrics["test-coverage"]
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

    scores = {
        metric.metric_id: metric
        for metric in DesignToCode().collect_evidence(context).metric_scores
    }
    functional = scores["functional"]
    coverage = scores["test-coverage"]
    stability = scores["verification-stability"]

    assert functional.passed is True
    assert functional.evidence == "owner=design-to-code, required_commands=1/1"
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
    missing_scores = {
        metric.metric_id: metric
        for metric in DesignToCode().collect_evidence(missing_coverage_context).metric_scores
    }
    assert missing_scores["test-coverage"].score == 1.0
    assert missing_scores["verification-stability"].passed is False


def test_scorer_context_type_import_is_available():
    assert ScorerContext is not None


def _evidence_output_payload(action_id: str) -> dict:
    return {
        "schema_version": "1",
        "runner_contract": "python-evidence-runner@2",
        "aliases": {action_id: action_id},
        "actions": [
            {
                "id": action_id,
                "kind": "command",
                "required": True,
                "status": "ok",
                "duration_sec": 0.1,
                "result": {
                    "command": ["true"],
                    "cwd": ".",
                    "duration_sec": 0.1,
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                    "timed_out": False,
                },
            }
        ],
        "artifacts": [],
        "metadata": {
            "action_count": 1,
            "error_count": 0,
            "required_error_count": 0,
            "workspace": "/workspace",
        },
    }


def test_absent_baseline_verification_evidence_fails_closed():
    context = SimpleNamespace(
        artifacts=SimpleNamespace(runner_evidence_artifacts={}),
    )

    with pytest.raises(
        VerificationEvidenceError,
        match="missing baseline verification evidence artifact",
    ):
        _load_baseline_verification_evidence(context)


def test_baseline_verification_evidence_loads_independently_from_final(tmp_path):
    final_path = tmp_path / "evidence.json"
    baseline_path = tmp_path / "baseline" / "evidence.json"
    baseline_path.parent.mkdir()
    final_path.write_text(
        json.dumps(_evidence_output_payload("final.build")),
        encoding="utf-8",
    )
    baseline_path.write_text(
        json.dumps(_evidence_output_payload("baseline.build")),
        encoding="utf-8",
    )
    context = SimpleNamespace(
        artifacts=SimpleNamespace(
            runner_evidence_artifacts={
                "evidence.json": str(final_path),
                "baseline/evidence.json": str(baseline_path),
            }
        ),
    )

    final_evidence = _load_verification_evidence(context)
    baseline_evidence = _load_baseline_verification_evidence(context)

    assert set(final_evidence.actions) == {"final.build"}
    assert set(baseline_evidence.actions) == {"baseline.build"}


def test_malformed_baseline_verification_evidence_fails_closed(tmp_path):
    baseline_path = tmp_path / "baseline" / "evidence.json"
    baseline_path.parent.mkdir()
    baseline_path.write_text(json.dumps({"schema_version": "1"}), encoding="utf-8")
    context = SimpleNamespace(
        artifacts=SimpleNamespace(
            runner_evidence_artifacts={"baseline/evidence.json": str(baseline_path)}
        ),
    )

    with pytest.raises(VerificationEvidenceError, match="invalid baseline verification evidence"):
        _load_baseline_verification_evidence(context)


def test_deterministic_helpers_cover_pass_fail_and_language_selection(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "page.tsx").write_text("import Ready from './ready';\nconst token = 'bad';\n")
    (src / "worker.py").write_text("from ledger import Ready\nlegacy_token = 'bad'\n")

    assert parse_judge_response("VERDICT: PASS\nEVIDENCE: ok").passed is True
    assert parse_judge_response("FAIL: missing requirement").passed is False
    assert parse_judge_response("unclear").evidence.startswith("Could not parse response")

    assert check_import_present(tmp_path, "Ready", languages=("typescript",))[0] is True
    assert check_import_present(tmp_path, "from ledger", languages=("typescript",))[0] is False
    assert check_import_present(tmp_path, "from ledger", languages=("python",))[0] is True
    assert (
        check_import_present(
            tmp_path / "missing",
            "Ready",
            languages=("typescript",),
        )[1]
        == "No source files found for languages: typescript"
    )
    assert check_file_exists(tmp_path, "src/*.tsx")[0] is True
    assert check_file_exists(tmp_path, "src/*.ts")[0] is False
    assert validate_safe_regex_pattern("(a+)+$")[0] is False
    assert validate_safe_regex_pattern("(a|aa)+$")[0] is False
    assert validate_safe_regex_pattern("a" * 513)[0] is False
    assert check_no_pattern(tmp_path, "token", languages=("typescript",))[0] is False
    assert check_no_pattern(tmp_path, "legacy_token", languages=("typescript",))[0] is True
    assert check_no_pattern(tmp_path, "legacy_token", languages=("python",))[0] is False
    assert check_no_pattern(tmp_path, "absent", languages=("typescript",))[0] is True
    assert check_no_pattern(tmp_path, "(a+)+$", languages=("typescript",))[0] is False
    assert check_no_pattern(tmp_path / "missing", "token", languages=("typescript",))[0] is True

    check = DeterministicCheck(
        type="file_exists",
        pattern="src/page.tsx",
        description="page exists",
    )
    result = run_deterministic_check(check, tmp_path, languages=("typescript",))
    assert result.passed is True
    assert score_requirement_checks([result]) == 1.0
    assert score_requirement_checks([]) == 1.0

    no_pattern = DeterministicCheck(
        type="no_pattern",
        pattern="absent",
        description="absent token stays absent",
    )
    assert (
        run_deterministic_check(
            no_pattern,
            tmp_path,
            languages=("typescript",),
        ).passed
        is True
    )
    unknown = DeterministicCheck.model_construct(
        type="unknown",
        pattern="anything",
        description="unknown checks fail closed",
    )
    assert (
        run_deterministic_check(
            unknown,
            tmp_path,
            languages=("typescript",),
        ).evidence
        == "Unknown check type: unknown"
    )


def test_requirements_coverage_scores_empty_and_missing_requirements(tmp_path):
    empty_context = SimpleNamespace(
        workspace=tmp_path,
        scenario_dir=tmp_path,
        scenario=SimpleNamespace(
            languages=["typescript"],
            requirements=SimpleNamespace(items=[]),
        ),
        execution=SimpleNamespace(),
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
                    languages=["typescript"],
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
                    ),
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


@pytest.mark.parametrize(
    ("scorer_id", "historical_metric_ids", "successor_metric_ids"),
    (
        (
            "bugfix",
            {
                "defect-resolution",
                "regression-protection",
                "change-containment",
                "verification-stability",
                "defect-evidence-completeness",
            },
            {
                "bugfix-defect-resolution-v2",
                "bugfix-regression-protection-v2",
                "bugfix-change-containment-v2",
                "bugfix-verification-stability-v2",
                "bugfix-defect-evidence-completeness-v2",
            },
        ),
        (
            "refactor",
            {
                "behavior-preservation",
                "structural-improvement",
                "public-contract-stability",
                "change-containment",
                "verification-stability",
            },
            {
                "refactor-behavior-preservation-v2",
                "refactor-structural-improvement-v2",
                "refactor-public-contract-stability-v2",
                "refactor-change-containment-v2",
                "refactor-verification-stability-v2",
            },
        ),
        (
            "test-generation",
            {
                "requirement-mapping",
                "assertion-strength",
                "coverage-lift",
                "production-code-guardrail",
                "verification-stability",
            },
            {
                "test-generation-requirement-mapping-v2",
                "test-generation-assertion-strength-v2",
                "test-generation-coverage-lift-v2",
                "test-generation-production-code-guardrail-v2",
                "test-generation-verification-stability-v2",
            },
        ),
        (
            "plan-to-code",
            {
                "plan-adherence",
                "planned-scope-coverage",
                "acceptance-evidence-completeness",
                "functional",
                "verification-stability",
            },
            {
                "plan-to-code-plan-adherence-v2",
                "plan-to-code-planned-scope-coverage-v2",
                "plan-to-code-acceptance-evidence-completeness-v2",
                "plan-to-code-functional-v2",
                "plan-to-code-verification-stability-v2",
            },
        ),
    ),
)
def test_task_conditioned_scorer_versions_have_disjoint_composable_metrics(
    scorer_id, historical_metric_ids, successor_metric_ids
):
    historical = load_scorer_definition(scorer_id, 1)
    successor = load_scorer_definition(scorer_id, 2)

    assert historical.version == 1
    assert successor.version == 2
    assert successor.extends is None
    assert {metric.id for metric in historical.metrics} == historical_metric_ids
    assert {metric.id for metric in successor.metrics} == successor_metric_ids
    assert historical_metric_ids.isdisjoint(successor_metric_ids)
    assert successor.evidence_dependencies == historical.evidence_dependencies

    scenario = SimpleNamespace(
        scorers=[
            SimpleNamespace(id=scorer_id, version=1, weight=0.4, config={}),
            SimpleNamespace(id=scorer_id, version=2, weight=0.4, config={}),
            SimpleNamespace(id="resource-efficiency", version=1, weight=0.2, config={}),
        ]
    )
    metrics = resolved_metrics(scenario)

    assert len({metric.id for metric in metrics}) == len(metrics)
    assert "resource-efficiency" in {metric.id for metric in metrics}


def test_task_conditioned_v2_metrics_are_mutually_and_version_disjoint() -> None:
    scorer_ids = ("bugfix", "refactor", "test-generation", "plan-to-code")
    v1_metric_id_sets = {
        scorer_id: {metric.id for metric in load_scorer_definition(scorer_id, 1).metrics}
        for scorer_id in scorer_ids
    }
    v2_metric_id_sets = {
        scorer_id: {metric.id for metric in load_scorer_definition(scorer_id, 2).metrics}
        for scorer_id in scorer_ids
    }

    for scorer_id, metric_ids in v2_metric_id_sets.items():
        assert metric_ids.isdisjoint(v1_metric_id_sets[scorer_id])

    all_v2_metric_ids = set().union(*v2_metric_id_sets.values())
    assert len(all_v2_metric_ids) == sum(map(len, v2_metric_id_sets.values()))
