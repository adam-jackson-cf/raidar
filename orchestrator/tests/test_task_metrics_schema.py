"""Schema tests for task metrics module configuration."""

import pytest
from pydantic import ValidationError

from raidar.schemas.task import TaskDefinition


def _base_task_payload() -> dict:
    return {
        "name": "schema-task",
        "version": "v001",
        "description": "schema test task",
        "difficulty": "medium",
        "category": "greenfield-ui",
        "timeout_sec": 1800,
        "scaffold": {"root": "scaffold"},
        "verification": {"gates": [], "required_commands": []},
        "compliance": {},
        "metrics": {
            "modules": [
                {"type": "core", "id": "functional"},
                {"type": "core", "id": "compliance"},
                {"type": "core", "id": "efficiency"},
                {"type": "core", "id": "run-validity"},
                {"type": "core", "id": "optimization"},
            ]
        },
        "prompt": {"entry": "prompt/task.md"},
    }


def test_metrics_modules_valid_payload_parses() -> None:
    task = TaskDefinition.model_validate(_base_task_payload())
    assert task.metric_module_ids() == [
        "functional",
        "compliance",
        "efficiency",
        "run-validity",
        "optimization",
    ]


def test_metrics_modules_reject_duplicate_ids() -> None:
    payload = _base_task_payload()
    payload["metrics"]["modules"].append({"type": "core", "id": "functional"})
    with pytest.raises(ValidationError, match="duplicate module ids"):
        TaskDefinition.model_validate(payload)


def test_metrics_modules_reject_unknown_type() -> None:
    payload = _base_task_payload()
    payload["metrics"]["modules"].append({"type": "unknown", "id": "mystery"})
    with pytest.raises(ValidationError):
        TaskDefinition.model_validate(payload)


def test_metrics_visual_odiff_requires_visual_config() -> None:
    payload = _base_task_payload()
    payload["metrics"]["modules"].append({"type": "core", "id": "visual-odiff"})
    with pytest.raises(ValidationError, match="visual-odiff without visual config"):
        TaskDefinition.model_validate(payload)


def test_metrics_coverage_threshold_requires_verification_threshold() -> None:
    payload = _base_task_payload()
    payload["metrics"]["modules"].append({"type": "core", "id": "coverage-threshold"})
    with pytest.raises(
        ValidationError, match="coverage-threshold without verification.coverage_threshold"
    ):
        TaskDefinition.model_validate(payload)


def test_metrics_requirements_requires_requirement_specs() -> None:
    payload = _base_task_payload()
    payload["metrics"]["modules"].append({"type": "core", "id": "requirements"})
    with pytest.raises(ValidationError, match="requirements without compliance.requirements"):
        TaskDefinition.model_validate(payload)


def test_metrics_llm_judge_requires_rubric() -> None:
    payload = _base_task_payload()
    payload["metrics"]["modules"].append({"type": "core", "id": "llm-judge"})
    with pytest.raises(ValidationError, match="llm-judge without compliance.llm_judge_rubric"):
        TaskDefinition.model_validate(payload)


def test_metrics_artifact_presence_requires_paths() -> None:
    payload = _base_task_payload()
    payload["metrics"]["modules"].append(
        {
            "type": "artifact_presence",
            "id": "artifact_presence",
            "config": {"required_paths": [], "path_match": "glob"},
        }
    )
    with pytest.raises(ValidationError):
        TaskDefinition.model_validate(payload)
