"""Schema tests for scenario metric configuration."""

import pytest
from pydantic import ValidationError

from raidar.schemas.scenario import ScenarioDefinition


def _base_scenario_payload() -> dict:
    return {
        "name": "schema-scenario",
        "scenario_revision": "v001",
        "description": "schema test scenario",
        "difficulty": "medium",
        "category": "greenfield-ui",
        "timeout_sec": 1800,
        "starter": {"root": "starter"},
        "verification": {"gates": [], "required_commands": []},
        "acceptance": {},
        "metrics": [
            {"type": "core", "id": "functional"},
            {"type": "core", "id": "acceptance"},
            {"type": "core", "id": "verification-stability"},
            {"type": "core", "id": "execution-validity"},
            {"type": "core", "id": "resource-efficiency"},
        ],
        "prompt": {"entry": "prompt/task.md"},
    }


def test_metrics_modules_valid_payload_parses() -> None:
    scenario = ScenarioDefinition.model_validate(_base_scenario_payload())
    assert scenario.metric_ids() == [
        "functional",
        "acceptance",
        "verification-stability",
        "execution-validity",
        "resource-efficiency",
    ]


def test_metrics_modules_reject_duplicate_ids() -> None:
    payload = _base_scenario_payload()
    payload["metrics"].append({"type": "core", "id": "functional"})
    with pytest.raises(ValidationError, match="duplicate metric ids"):
        ScenarioDefinition.model_validate(payload)


def test_metrics_modules_reject_unknown_type() -> None:
    payload = _base_scenario_payload()
    payload["metrics"].append({"type": "unknown", "id": "mystery"})
    with pytest.raises(ValidationError):
        ScenarioDefinition.model_validate(payload)


def test_metrics_visual_regression_requires_visual_config() -> None:
    payload = _base_scenario_payload()
    payload["metrics"].append({"type": "core", "id": "visual-regression"})
    with pytest.raises(ValidationError, match="visual-regression without visual config"):
        ScenarioDefinition.model_validate(payload)


def test_metrics_test_coverage_requires_verification_threshold() -> None:
    payload = _base_scenario_payload()
    payload["metrics"].append({"type": "core", "id": "test-coverage"})
    with pytest.raises(
        ValidationError, match="test-coverage without verification.coverage_threshold"
    ):
        ScenarioDefinition.model_validate(payload)


def test_metrics_requirements_coverage_requires_requirement_specs() -> None:
    payload = _base_scenario_payload()
    payload["metrics"].append({"type": "core", "id": "requirements-coverage"})
    with pytest.raises(
        ValidationError,
        match="requirements-coverage without acceptance.requirements",
    ):
        ScenarioDefinition.model_validate(payload)


def test_metrics_llm_judge_requires_rubric() -> None:
    payload = _base_scenario_payload()
    payload["metrics"].append({"type": "core", "id": "llm-judge"})
    with pytest.raises(ValidationError, match="llm-judge without acceptance.llm_judge_rubric"):
        ScenarioDefinition.model_validate(payload)


def test_metrics_artifact_checks_require_paths() -> None:
    payload = _base_scenario_payload()
    payload["metrics"].append(
        {
            "type": "artifact-checks",
            "id": "artifact-checks",
            "config": {"required_paths": [], "path_match": "glob"},
        }
    )
    with pytest.raises(ValidationError):
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
        "reference_image": "reference.png",
        "screenshot_command": ["bun", "run", "capture-screenshot", ">", "out.png"],
    }
    payload["metrics"].append({"type": "core", "id": "visual-regression"})

    with pytest.raises(ValidationError, match="must not include shell operators"):
        ScenarioDefinition.model_validate(payload)
