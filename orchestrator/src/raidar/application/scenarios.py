"""Scenario-oriented application services."""

from __future__ import annotations

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
from raidar.runner import load_scenario
from raidar.scenario_clone import ScenarioCloneResult
from raidar.scenario_clone import clone_scenario_revision as clone_revision


def init_scenario(request: ScenarioInitRequest) -> ScenarioInitResult:
    """Create a new versioned scenario descriptor with prompt artifacts and rules."""

    scenario_root = request.path.resolve()
    scenario_name = request.name or scenario_root.name
    revision_dir = scenario_root / request.scenario_revision
    scenario_yaml = revision_dir / "scenario.yaml"
    if scenario_yaml.exists():
        raise FileExistsError(f"Scenario already exists: {scenario_yaml}")

    rules_dir = revision_dir / "rules"
    prompt_dir = revision_dir / "prompt"
    rules_dir.mkdir(parents=True, exist_ok=True)
    prompt_dir.mkdir(parents=True, exist_ok=True)

    scenario_doc: dict[str, Any] = {
        "name": scenario_name,
        "scenario_revision": request.scenario_revision,
        "description": f"Scenario definition for {scenario_name}",
        "difficulty": request.difficulty,
        "category": request.category,
        "timeout_sec": request.timeout_sec,
        "dockerfile": "./Dockerfile",
        "test_scripts": [],
        "starter": {"root": request.starter_root},
        "verification": {
            "max_gate_failures": 3,
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
        "acceptance": {
            "deterministic_checks": [
                {
                    "type": "no_pattern",
                    "pattern": "TODO",
                    "description": "No TODO markers remain in production files",
                }
            ],
            "requirements": [],
            "llm_judge_rubric": [],
        },
        "metrics": [
            {"type": "core", "id": "functional"},
            {"type": "core", "id": "acceptance"},
            {"type": "core", "id": "verification-stability"},
            {"type": "core", "id": "execution-validity"},
            {"type": "core", "id": "resource-efficiency"},
        ],
        "prompt": {"entry": request.prompt_entry, "includes": []},
    }
    _write_yaml_mapping(scenario_yaml, scenario_doc)

    prompt_path = revision_dir / request.prompt_entry
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        (
            "Implement the requested feature in the starter application.\n\n"
            "Run all required verification commands before completion and "
            "report only after they pass.\n"
        ),
        encoding="utf-8",
    )

    rule_text = (
        "Follow the scenario prompt exactly. Run required verification commands before completion."
    )
    for filename in sorted(set(SYSTEM_RULES.values())):
        (rules_dir / filename).write_text(rule_text + "\n", encoding="utf-8")

    return ScenarioInitResult(
        scenario_root=scenario_root,
        scenario_name=scenario_name,
        scenario_revision=request.scenario_revision,
        revision_dir=revision_dir,
        scenario_yaml=scenario_yaml,
        prompt_path=prompt_path,
        rules_dir=rules_dir,
        starter_root=request.starter_root,
    )


def validate_scenario(path: Path) -> ScenarioValidationResult:
    """Validate a scenario document and return the typed result."""

    scenario_yaml = resolve_scenario_yaml(path)
    return ScenarioValidationResult(
        scenario_path=scenario_yaml,
        scenario=load_scenario(scenario_yaml),
    )


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
    return max(candidates, key=_scenario_revision_sort_key)


def _scenario_revision_sort_key(scenario_yaml: Path) -> tuple[int, str]:
    revision_dir = scenario_yaml.parent.name
    if not revision_dir.startswith("v") or not revision_dir[1:].isdigit():
        return (-1, revision_dir)
    return (int(revision_dir[1:]), revision_dir)


def _write_yaml_mapping(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)
