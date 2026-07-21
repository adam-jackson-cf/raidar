"""Regression coverage for canonical scenario taxonomy and revision lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from raidar.application.models import ScenarioInitRequest
from raidar.application.scenarios import init_scenario
from raidar.scenario_clone import clone_scenario_revision
from raidar.schemas.scenario import ScenarioDefinition


def _init_request(
    path: Path,
    **overrides: object,
) -> ScenarioInitRequest:
    values: dict[str, object] = {
        "path": path,
        "name": "taxonomy-scenario",
        "display_name": "Taxonomy scenario",
        "scenario_revision": "v001",
        "starter_root": "workspace",
        "prompt_entry": "prompt/task.md",
        "difficulty": "medium",
        "task_kind": "feature_implementation",
        "delivery_mode": "direct_implementation",
        "workspace_state": "new",
        "starting_conditions": [],
        "languages": ["python"],
        "evaluation_role": "benchmark",
        "catalog_status": "active",
        "timeout_sec": 1800,
        "template": "python-pytest",
        "family_id": None,
        "variant_axes": {},
    }
    values.update(overrides)
    return ScenarioInitRequest(**values)


def _scenario_payload(tmp_path: Path) -> dict[str, object]:
    result = init_scenario(_init_request(tmp_path / "scenario"))
    return ScenarioDefinition.model_validate(
        yaml.safe_load(result.scenario_yaml.read_text(encoding="utf-8"))
    ).model_dump(mode="python")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_kind", "migration"),
        ("delivery_mode", "review_only"),
        ("workspace_state", "unknown"),
        ("evaluation_role", "research"),
        ("catalog_status", "draft"),
    ],
)
def test_scenario_taxonomy_rejects_unknown_closed_values(
    tmp_path: Path, field: str, value: str
) -> None:
    payload = _scenario_payload(tmp_path)
    payload[field] = value

    with pytest.raises(ValidationError):
        ScenarioDefinition.model_validate(payload)


@pytest.mark.parametrize(
    ("task_kind", "starting_conditions"),
    [
        ("defect_repair", []),
        ("feature_implementation", ["known_defect"]),
    ],
)
def test_known_defect_is_exclusive_to_defect_repair(
    tmp_path: Path, task_kind: str, starting_conditions: list[str]
) -> None:
    payload = _scenario_payload(tmp_path)
    payload["task_kind"] = task_kind
    payload["starting_conditions"] = starting_conditions

    with pytest.raises(ValidationError):
        ScenarioDefinition.model_validate(payload)


def test_new_workspace_rejects_starting_conditions(tmp_path: Path) -> None:
    payload = _scenario_payload(tmp_path)
    payload["starting_conditions"] = ["legacy_structure"]

    with pytest.raises(ValidationError):
        ScenarioDefinition.model_validate(payload)


def test_variant_axes_require_a_family_id(tmp_path: Path) -> None:
    payload = _scenario_payload(tmp_path)
    payload["variant_axes"] = {"framework": "django"}

    with pytest.raises(ValidationError):
        ScenarioDefinition.model_validate(payload)


def test_initialisation_and_revision_clone_preserve_canonical_taxonomy(tmp_path: Path) -> None:
    scenario_root = tmp_path / "scenario"
    initial = init_scenario(
        _init_request(
            scenario_root,
            task_kind="defect_repair",
            workspace_state="existing",
            starting_conditions=["known_defect"],
            family_id="taxonomy-family",
            variant_axes={"framework": "django"},
        )
    )

    clone = clone_scenario_revision(
        scenario_root=scenario_root,
        source_revision="v001",
        target_revision="v002",
    )
    initial_payload = yaml.safe_load(initial.scenario_yaml.read_text(encoding="utf-8"))
    cloned_payload = yaml.safe_load(clone.target_scenario_yaml.read_text(encoding="utf-8"))
    expected_taxonomy = {
        "task_kind": "defect_repair",
        "delivery_mode": "direct_implementation",
        "workspace_state": "existing",
        "starting_conditions": ["known_defect"],
        "languages": ["python"],
        "evaluation_role": "benchmark",
        "catalog_status": "active",
        "family_id": "taxonomy-family",
        "variant_axes": {"framework": "django"},
    }

    assert {key: initial_payload[key] for key in expected_taxonomy} == expected_taxonomy
    assert {key: cloned_payload[key] for key in expected_taxonomy} == expected_taxonomy
    assert "category" not in initial_payload
    assert "category" not in cloned_payload
    assert cloned_payload["scenario_revision"] == "v002"
    assert cloned_payload["parent_revision"] == "v001"
