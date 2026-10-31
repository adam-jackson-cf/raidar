"""A throwaway repo root holding copies of the real codex agent and raidar/one-shot workflow."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

from raidar.contract.paths import agents_root, releases_file, workflows_root
from raidar.subject.agent.definition import load_agent
from raidar.subject.workflow.definition import WORKFLOW_FILE, load_workflow

REAL_LAB = Path(__file__).resolve().parents[2] / "lab"


def release_tree(root: Path, *, register: bool = True) -> Path:
    """Copy the real codex agent and raidar/one-shot workflow under ``root``; register both."""
    agents, workflows = agents_root(root), workflows_root(root)
    agents.mkdir(parents=True)
    shutil.copy(REAL_LAB / "agents" / "codex.yaml", agents / "codex.yaml")
    shutil.copytree(
        REAL_LAB / "workflows" / "raidar" / "one-shot", workflows / "raidar" / "one-shot"
    )
    if register:
        write_register(root)
    return root


def write_register(root: Path, extra_agents: dict[str, str] | None = None) -> None:
    """Register the tree's codex and raidar/one-shot at their current hashes (plus agents)."""
    document: dict[str, Any] = {
        "agents": {
            "codex@1": load_agent(agents_root(root), "codex").digest,
            **(extra_agents or {}),
        },
        "workflows": {
            "raidar/one-shot@1": load_workflow(workflows_root(root), "raidar/one-shot").digest
        },
    }
    releases_file(root).write_text(yaml.safe_dump(document), encoding="utf-8")


def edit_agent(root: Path, mutate: Any) -> None:
    """Rewrite the tree's codex.yaml through ``mutate(document)``."""
    path = agents_root(root) / "codex.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def write_workflow(root: Path, document: dict[str, Any], name: str = "raidar/one-shot") -> None:
    """Replace the tree's workflow ``name`` with ``document``."""
    path = workflows_root(root) / name / WORKFLOW_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
