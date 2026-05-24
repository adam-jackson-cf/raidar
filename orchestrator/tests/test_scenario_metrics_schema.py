"""Schema tests for scenario scorer configuration."""

import pytest
from pydantic import ValidationError

from raidar.schemas.scenario import ScenarioDefinition
from raidar.scorers.registry import load_scorer_definition


def _requirement() -> dict:
    return {
        "id": "req-marker",
        "description": "Marker text exists.",
        "check": {
            "type": "import_present",
            "pattern": "Ready",
            "description": "Marker text exists",
        },
        "required_test_evidence": [],
    }


def _base_scenario_payload() -> dict:
    return {
        "name": "schema-scenario",
        "scenario_revision": "v001",
        "description": "schema test scenario",
        "difficulty": "medium",
        "category": "greenfield-ui",
        "timeout_sec": 1800,
        "starter": {"root": "starter"},
        "verification": {
            "coverage_threshold": 0.8,
            "gates": [],
            "required_commands": [],
        },
        "acceptance": {"requirements": [_requirement()]},
        "scorers": [
            {
                "id": "typescript-code-task",
                "version": 1,
                "weight": 0.9,
                "config": {
                    "artifact-checks": {
                        "required_paths": ["src/lib/math.ts"],
                        "path_match": "glob",
                    }
                },
            },
            {"id": "resource-efficiency", "version": 1, "weight": 0.1},
        ],
        "prompt": {"entry": "prompt/task.md"},
    }


def test_scorer_refs_valid_payload_parses() -> None:
    scenario = ScenarioDefinition.model_validate(_base_scenario_payload())
    assert scenario.scorer_ids() == ["typescript-code-task@1", "resource-efficiency@1"]
    assert scenario.metric_ids() == [
        "functional",
        "code-quality",
        "test-coverage",
        "artifact-checks",
        "verification-stability",
        "resource-efficiency",
    ]


def test_parent_revision_must_differ_from_scenario_revision() -> None:
    payload = _base_scenario_payload()
    payload["parent_revision"] = "v001"

    with pytest.raises(ValidationError, match="parent_revision must differ"):
        ScenarioDefinition.model_validate(payload)


@pytest.mark.parametrize("legacy_field", ["metrics", "score_profile"])
def test_scenario_rejects_removed_top_level_fields(legacy_field: str) -> None:
    payload = _base_scenario_payload()
    payload[legacy_field] = []

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScenarioDefinition.model_validate(payload)


def test_scenario_rejects_removed_llm_judge_rubric_field() -> None:
    payload = _base_scenario_payload()
    payload["acceptance"]["llm_judge_rubric"] = []

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScenarioDefinition.model_validate(payload)


def test_min_quality_score_requires_quality_scorer() -> None:
    payload = _base_scenario_payload()
    payload["scorers"] = [{"id": "resource-efficiency", "version": 1, "weight": 1.0}]

    with pytest.raises(ValidationError, match="min_quality_score requires"):
        ScenarioDefinition.model_validate(payload)


def test_resource_only_scorer_allows_zero_min_quality_score() -> None:
    payload = _base_scenario_payload()
    payload["verification"]["min_quality_score"] = 0.0
    payload["scorers"] = [{"id": "resource-efficiency", "version": 1, "weight": 1.0}]

    scenario = ScenarioDefinition.model_validate(payload)

    assert scenario.scorer_ids() == ["resource-efficiency@1"]


def test_scorer_refs_reject_duplicate_refs() -> None:
    payload = _base_scenario_payload()
    payload["scorers"].append({"id": "resource-efficiency", "version": 1, "weight": 0.2})
    with pytest.raises(ValidationError, match="duplicate scorer references"):
        ScenarioDefinition.model_validate(payload)


def test_scorer_refs_reject_unknown_scorer() -> None:
    payload = _base_scenario_payload()
    payload["scorers"] = [{"id": "missing-scorer", "version": 1, "weight": 1.0}]
    with pytest.raises(ValidationError, match="Unknown scorer definition"):
        ScenarioDefinition.model_validate(payload)


def test_scorer_refs_reject_proposed_scorer() -> None:
    payload = _base_scenario_payload()
    payload["scorers"] = [{"id": "plan-to-code", "version": 1, "weight": 1.0}]
    with pytest.raises(ValidationError, match="is proposed"):
        ScenarioDefinition.model_validate(payload)


def test_code_task_family_is_not_attachable() -> None:
    payload = _base_scenario_payload()
    payload["scorers"] = [{"id": "code-task", "version": 1, "weight": 1.0}]
    with pytest.raises(ValidationError, match="is proposed"):
        ScenarioDefinition.model_validate(payload)


def test_scorer_refs_reject_invalid_weights() -> None:
    payload = _base_scenario_payload()
    payload["scorers"][0]["weight"] = 0
    with pytest.raises(ValidationError):
        ScenarioDefinition.model_validate(payload)


def test_scorer_metric_dependencies_are_validated() -> None:
    payload = _base_scenario_payload()
    payload["verification"].pop("coverage_threshold")
    with pytest.raises(
        ValidationError, match="test-coverage without verification.coverage_threshold"
    ):
        ScenarioDefinition.model_validate(payload)


def test_design_to_code_does_not_include_legacy_requirements_coverage_metric() -> None:
    scorer = load_scorer_definition("design-to-code", 1)

    assert "requirements-coverage" not in [metric.id for metric in scorer.metrics]


def test_design_to_code_is_deterministic() -> None:
    payload = _base_scenario_payload()
    payload["verification"]["coverage_threshold"] = 0.8
    payload["visual"] = _visual_payload()
    payload["scorers"] = [{"id": "design-to-code", "version": 1, "weight": 1.0}]

    scenario = ScenarioDefinition.model_validate(payload)

    assert all(metric.type != "llm-as-judge" for metric in scenario.resolved_metrics())


def test_plan_to_code_defines_scorer_owned_plan_quality_judge() -> None:
    scorer = load_scorer_definition("plan-to-code", 1)

    metric = next(metric for metric in scorer.metrics if metric.id == "plan-quality")

    assert metric.type == "llm-as-judge"
    assert metric.config["judge"] == "judges/plan-judge.toml"


def test_requirements_defines_requirements_adherence_judge() -> None:
    scorer = load_scorer_definition("requirements", 1)

    metric = next(metric for metric in scorer.metrics if metric.id == "requirements-adherence")

    assert metric.type == "llm-as-judge"
    assert metric.config["judge"] == "judges/requirements-adherence.toml"


def test_python_code_task_extends_code_task_metric_interface() -> None:
    family = load_scorer_definition("code-task", 1)
    scorer = load_scorer_definition("python-code-task", 1)

    assert scorer.status == "proposed"
    assert scorer.extends == family.id
    assert scorer.runtime == "python"
    assert [metric.id for metric in scorer.metrics] == [metric.id for metric in family.metrics]
    assert [metric.weight for metric in scorer.metrics] == [
        metric.weight for metric in family.metrics
    ]


def test_scorer_refs_reject_unknown_metric_config_keys() -> None:
    payload = _base_scenario_payload()
    payload["scorers"][0]["config"] = {"llm-as-judge": {"judge": "judges/reviewer.md"}}

    with pytest.raises(ValidationError, match="metrics not in scorer definition"):
        ScenarioDefinition.model_validate(payload)


def test_required_commands_reject_shell_wrappers() -> None:
    payload = _base_scenario_payload()
    payload["verification"]["required_commands"] = [["bash", "-lc", "bun run lint"]]

    with pytest.raises(ValidationError, match="must be an argv list"):
        ScenarioDefinition.model_validate(payload)


def test_gate_commands_reject_shell_operators() -> None:
    payload = _base_scenario_payload()
    payload["verification"]["gates"] = [
        {"name": "lint", "command": ["bun", "run", "lint&&bun", "run", "test"]}
    ]

    with pytest.raises(ValidationError, match="must not include shell operators"):
        ScenarioDefinition.model_validate(payload)


def test_screenshot_command_rejects_shell_redirection() -> None:
    payload = _base_scenario_payload()
    payload["visual"] = {
        **_visual_payload(),
        "screenshot_command": ["bun", "run", "capture-screenshot", ">", "out.png"],
    }
    payload["scorers"] = [{"id": "design-to-code", "version": 1, "weight": 1.0}]

    with pytest.raises(ValidationError, match="must not include shell operators"):
        ScenarioDefinition.model_validate(payload)


def test_setup_actions_reject_shell_wrappers() -> None:
    payload = _base_scenario_payload()
    payload["verification"]["setup_actions"] = [["bash", "-lc", "git init"]]

    with pytest.raises(ValidationError, match="must be an argv list"):
        ScenarioDefinition.model_validate(payload)


def test_visual_viewport_parses_when_configured() -> None:
    payload = _base_scenario_payload()
    payload["visual"] = _visual_payload()
    payload["scorers"] = [{"id": "design-to-code", "version": 1, "weight": 1.0}]

    scenario = ScenarioDefinition.model_validate(payload)

    assert scenario.visual is not None
    assert scenario.visual.viewport is not None
    assert scenario.visual.viewport.width == 1440
    assert scenario.visual.viewport.height == 1024
    assert scenario.visual.scoring.weights.global_weight == 0.25
    assert scenario.visual.pass_policy.minimum_score == 70


def test_workflow_atomic_commits_flag_parses() -> None:
    payload = _base_scenario_payload()
    payload["verification"]["workflow"] = {"atomic_commits_required": True}

    scenario = ScenarioDefinition.model_validate(payload)

    assert scenario.verification.workflow.atomic_commits_required is True


def _visual_payload() -> dict:
    return {
        "reference_image": "reference.png",
        "screenshot_command": ["bun", "run", "capture-screenshot"],
        "viewport": {"width": 1440, "height": 1024},
        "scoring": {
            "weights": {
                "global": 0.25,
                "regional": 0.45,
                "worst_region": 0.25,
                "region_pass_rate": 0.05,
            },
            "bands": {
                "global": {"lower": 0.85, "upper": 0.96},
                "regional": {"lower": 0.8, "upper": 0.95},
                "worst_region": {"lower": 0.75, "upper": 0.94},
            },
            "gamma": 2,
            "region_pass_threshold": 0.9,
        },
        "pass_policy": {
            "fail_if_global_below": 0.9,
            "fail_if_worst_region_below": 0.85,
            "minimum_score": 70,
            "minimum_region_pass_rate": 0.75,
            "minimum_worst_region": 0.88,
            "high_fidelity_score": 85,
            "high_fidelity_global": 0.95,
            "high_fidelity_worst_region": 0.92,
        },
        "regions": [],
    }
