"""Stage code digests: the identity of the Raidar code that runs each enabled stage type.

A workflow's content hash moves only when its definition does; the code that carries a stage
out can change behaviour without touching any definition. The lock therefore records, per
arm, one digest per enabled stage type over that stage's modules, the agent's adapter modules
and the Harbor release that implements the native agent. Pooling and smoke admission compare
these digests and never let a difference be overridden.

Modules are hashed as normalised syntax trees (``raidar.contract.source_digest``), so a comment
or docstring edit leaves a digest unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib.metadata import version
from pathlib import Path

from raidar.contract.canonical import digest_json
from raidar.contract.source_digest import SOURCE_DIGEST_SCHEME, python_source_digest
from raidar.subject.agent.config import AgentName
from raidar.subject.workflow.definition import StageType

RAIDAR_DIR = Path(__file__).resolve().parents[2]

STAGE_MODULES: Mapping[StageType, tuple[str, ...]] = {
    "build": (
        "subject/workflow/runner.py",
        "subject/workflow/inner.py",
        "subject/workflow/definition.py",
    ),
}
"""Modules (below ``raidar/``) that carry out each implemented stage type."""

ADAPTER_MODULES: Mapping[AgentName, tuple[str, ...]] = {
    AgentName.CODEX: (
        "subject/agent/adapters/base.py",
        "subject/agent/adapters/harbor_cli.py",
        "subject/agent/adapters/codex_cli.py",
    ),
    AgentName.CLAUDE_CODE: (
        "subject/agent/adapters/base.py",
        "subject/agent/adapters/harbor_cli.py",
        "subject/agent/adapters/claude_code_cli.py",
    ),
}
"""Adapter modules (below ``raidar/``) of each agent a stage drives."""


def stage_code_digest(stage: StageType, adapter: AgentName) -> str:
    """One stage type's code identity for an agent adapter."""
    modules = (*STAGE_MODULES[stage], *ADAPTER_MODULES[adapter])
    return digest_json(
        {
            "scheme": SOURCE_DIGEST_SCHEME,
            "harbor": version("harbor"),
            "modules": {name: python_source_digest(RAIDAR_DIR / name) for name in modules},
        }
    )


def stage_code_digests(stages: Iterable[StageType], adapter: AgentName) -> dict[str, str]:
    """``{stage type: digest}`` for each enabled stage."""
    return {stage: stage_code_digest(stage, adapter) for stage in stages}
