"""Scenario-oriented application services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from raidar.agents.rules import SYSTEM_RULES
from raidar.application.models import (
    ScenarioCloneRequest,
    ScenarioInitRequest,
    ScenarioInitResult,
    ScenarioValidationResult,
)
from raidar.application.scenario_catalog import load_scenario
from raidar.scenario_clone import ScenarioCloneResult
from raidar.scenario_clone import clone_scenario_revision as clone_revision

SCENARIO_PROMPT_TEXT = (
    "Implement the requested feature in the starter application.\n\n"
    "Run all required verification commands before completion and "
    "report only after they pass.\n"
)
SCENARIO_RULE_TEXT = (
    "Follow the scenario prompt exactly. Run required verification commands before completion."
)


@dataclass(frozen=True)
class ScenarioInitLayout:
    scenario_root: Path
    scenario_name: str
    revision_dir: Path
    scenario_yaml: Path
    prompt_path: Path
    rules_dir: Path


def init_scenario(request: ScenarioInitRequest) -> ScenarioInitResult:
    """Create a new versioned scenario descriptor with prompt artifacts and rules."""

    layout = _scenario_init_layout(request)
    _ensure_new_scenario(layout)
    _create_scenario_init_dirs(layout)
    write_scenario_document(layout.scenario_yaml, _scenario_doc(request, layout))
    _write_scenario_prompt(layout)
    _write_scenario_rules(layout)
    return _scenario_init_result(request, layout)


def _scenario_init_layout(request: ScenarioInitRequest) -> ScenarioInitLayout:
    scenario_root = request.path.resolve()
    revision_dir = scenario_root / request.scenario_revision
    return ScenarioInitLayout(
        scenario_root=scenario_root,
        scenario_name=request.name or scenario_root.name,
        revision_dir=revision_dir,
        scenario_yaml=revision_dir / "scenario.yaml",
        prompt_path=revision_dir / request.prompt_entry,
        rules_dir=revision_dir / "rules",
    )


def _ensure_new_scenario(layout: ScenarioInitLayout) -> None:
    if layout.scenario_yaml.exists():
        raise FileExistsError(f"Scenario already exists: {layout.scenario_yaml}")


def _create_scenario_init_dirs(layout: ScenarioInitLayout) -> None:
    layout.rules_dir.mkdir(parents=True, exist_ok=True)
    (layout.revision_dir / "prompt").mkdir(parents=True, exist_ok=True)


def _scenario_doc(request: ScenarioInitRequest, layout: ScenarioInitLayout) -> dict[str, Any]:
    return {
        "name": layout.scenario_name,
        "scenario_revision": request.scenario_revision,
        "parent_revision": None,
        "description": f"Scenario definition for {layout.scenario_name}",
        "difficulty": request.difficulty,
        "category": request.category,
        "timeout_sec": request.timeout_sec,
        "dockerfile": "./Dockerfile",
        "test_scripts": [],
        "starter": {"root": request.starter_root},
        "verification": {
            "max_gate_failures": 3,
            "coverage_threshold": 0.8,
            "min_quality_score": 0.8,
            "required_commands": [
                ["bun", "run", "typecheck"],
                ["bun", "run", "lint"],
            ],
            "gates": [
                {"name": "typecheck", "command": ["bun", "run", "typecheck"]},
                {"name": "lint", "command": ["bun", "run", "lint"]},
            ],
        },
        "requirements": {
            "items": [
                {
                    "id": "req-no-todo",
                    "description": "No TODO markers remain in production files.",
                    "check": {
                        "type": "no_pattern",
                        "pattern": "TODO",
                        "description": "No TODO markers remain in production files",
                    },
                    "required_test_evidence": [],
                }
            ],
        },
        "scorers": [
            {"id": "typescript-code-task", "version": 1, "weight": 0.9},
            {"id": "resource-efficiency", "version": 1, "weight": 0.1},
        ],
        "prompt": {"entry": request.prompt_entry, "includes": []},
    }


def _write_scenario_prompt(layout: ScenarioInitLayout) -> None:
    layout.prompt_path.parent.mkdir(parents=True, exist_ok=True)
    layout.prompt_path.write_text(SCENARIO_PROMPT_TEXT, encoding="utf-8")


def _write_scenario_rules(layout: ScenarioInitLayout) -> None:
    for filename in sorted(set(SYSTEM_RULES.values())):
        (layout.rules_dir / filename).write_text(SCENARIO_RULE_TEXT + "\n", encoding="utf-8")


def _scenario_init_result(
    request: ScenarioInitRequest, layout: ScenarioInitLayout
) -> ScenarioInitResult:
    return ScenarioInitResult(
        scenario_root=layout.scenario_root,
        scenario_name=layout.scenario_name,
        scenario_revision=request.scenario_revision,
        parent_revision=None,
        revision_dir=layout.revision_dir,
        scenario_yaml=layout.scenario_yaml,
        prompt_path=layout.prompt_path,
        rules_dir=layout.rules_dir,
        starter_root=request.starter_root,
    )


def validate_scenario(path: Path) -> ScenarioValidationResult:
    """Validate a scenario document and return the typed result."""

    scenario_yaml = resolve_scenario_yaml(path)
    scenario = load_scenario(scenario_yaml)
    _validate_scenario_files(scenario_yaml.parent, scenario)
    return ScenarioValidationResult(
        scenario_path=scenario_yaml,
        scenario=scenario,
    )


def _validate_scenario_files(scenario_dir: Path, scenario) -> None:
    del scenario_dir
    scenario.resolved_metrics()


def clone_scenario_revision(request: ScenarioCloneRequest) -> ScenarioCloneResult:
    """Clone a scenario revision through the canonical service boundary."""

    return clone_revision(
        scenario_root=request.path.resolve(),
        source_revision=request.from_revision,
        target_revision=request.to_revision,
    )


def resolve_scenario_yaml(path: Path) -> Path:
    """Resolve a scenario root or revision directory to its scenario.yaml."""

    resolved = path.resolve()
    if resolved.is_file():
        return resolved
    scenario_yaml = resolved / "scenario.yaml"
    if scenario_yaml.is_file():
        return scenario_yaml
    candidates = list(resolved.glob("v*/scenario.yaml"))
    if not candidates:
        raise FileNotFoundError(f"scenario.yaml not found in {resolved}")
    return max(candidates, key=scenario_revision_sort_key)


def scenario_revision_sort_key(scenario_yaml: Path) -> tuple[int, str]:
    """Sort scenario.yaml paths by numeric revision directory when possible."""

    revision_dir = scenario_yaml.parent.name
    if not revision_dir.startswith("v") or not revision_dir[1:].isdigit():
        return (-1, revision_dir)
    return (int(revision_dir[1:]), revision_dir)


def scenario_revision_paths(scenario_root: Path) -> list[Path]:
    """Return scenario revisions sorted by canonical revision ordering."""

    if not scenario_root.is_dir():
        return []
    return sorted(scenario_root.glob("v*/scenario.yaml"), key=scenario_revision_sort_key)


def load_scenario_document(path: Path) -> dict[str, Any]:
    """Load a scenario YAML document as a mapping."""

    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Scenario document must be a mapping: {path}")
    return payload


def write_scenario_document(path: Path, payload: dict[str, Any]) -> None:
    """Write a scenario YAML mapping with stable key order."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)
