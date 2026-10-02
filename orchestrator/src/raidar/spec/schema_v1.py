"""Legacy ``schema_version: 1`` experiment schema: loadable read-only.

Schema v1 is the vocabulary before the Harbor alignment (requirements change
log 2026-10-02): ``runtime.environment``, ``agents[].harness`` (CLI install ids
such as ``codex-cli``), ``assessment.headline_policy`` and ``analysis.blocks``.
A v1 file still validates (``raidar validate``) and converts to schema v2 with
``upgrade_document`` (``raidar upgrade-spec``), but it can no longer be locked,
planned or run. ``raidar migrate-job`` uses the same translation to move jobs
executed under a v1 lock onto the v2 lock of the same experiment.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from raidar.agents.config import Harness
from raidar.spec.schema import (
    PINNED_VERSION_PATTERN,
    ArmSpec,
    ComponentUse,
    ConditionSpec,
    Identifier,
    InventoryPolicySpec,
    ProblemEntry,
    ProtocolSpec,
    Reference,
    SpecModel,
)

LEGACY_SCHEMA_VERSION = 1


class RuntimeSpecV1(SpecModel):
    language: Literal["python"] = "python"
    environment: Literal["docker"] = "docker"
    compose_init: bool = True


class AgentEntryV1(SpecModel):
    id: Identifier
    harness: Harness
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    reasoning_effort: str | None = None
    version: str = Field(pattern=PINNED_VERSION_PATTERN)


class AssessmentSpecV1(SpecModel):
    scorers: tuple[Reference, ...] = Field(min_length=1)
    headline_policy: Reference | None = None
    inventory_policy: InventoryPolicySpec = InventoryPolicySpec()
    scorer_config: dict[Reference, dict[str, Any]] = Field(default_factory=dict)


class AnalysisSpecV1(SpecModel):
    baseline_arm: Identifier
    blocks: int = Field(gt=0)
    seed: int = Field(ge=0)
    concurrency: int = Field(default=1, gt=0)
    min_pairs: int = Field(default=5, ge=2)
    max_exclusion_fraction: float = Field(default=0.2, ge=0.0, le=1.0)
    alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    min_effects: dict[str, float] = Field(default_factory=dict)


FactorNounV1 = Literal[
    "condition",
    "agent",
    "agent.harness",
    "agent.provider",
    "agent.model",
    "agent.reasoning_effort",
    "agent.version",
]


class ExperimentSpecV1(SpecModel):
    """A schema v1 experiment exactly as it was written (read-only)."""

    schema_version: Literal[1]
    id: Identifier
    description: str = ""
    problems: tuple[ProblemEntry, ...] = Field(min_length=1)
    runtime: RuntimeSpecV1 = RuntimeSpecV1()
    agents: tuple[AgentEntryV1, ...] = Field(min_length=1)
    protocol: ProtocolSpec = ProtocolSpec()
    factors: tuple[FactorNounV1, ...] = ()
    shared_components: tuple[ComponentUse, ...] = ()
    conditions: tuple[ConditionSpec, ...] = Field(min_length=1)
    arms: tuple[ArmSpec, ...] = Field(min_length=1)
    assessment: AssessmentSpecV1
    analysis: AnalysisSpecV1


FACTOR_RENAMES = {"agent.harness": "agent.name"}


def upgrade_agent(agent: dict[str, Any]) -> dict[str, Any]:
    """A v1 agent entry in v2 form: ``harness`` (CLI id) becomes ``name`` (Harbor agent)."""
    from raidar.agents.config import agent_name_for

    upgraded = {key: value for key, value in agent.items() if key != "harness"}
    upgraded["name"] = agent_name_for(str(agent["harness"])).value
    return upgraded


def _environment(runtime: dict[str, Any]) -> dict[str, Any]:
    renames = (("environment", "type"), ("compose_init", "compose_init"))
    return {new: runtime[old] for old, new in renames if old in runtime}


def _renamed(section: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    renamed = dict(section)
    if old in renamed:
        renamed[new] = renamed.pop(old)
    return renamed


_TRANSLATED = {"schema_version", "id", "runtime", "agents", "factors", "assessment", "analysis"}
_SECTION_RENAMES = (("assessment", "headline_policy", "metric"), ("analysis", "blocks", "attempts"))


def _unchanged(document: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items() if key not in _TRANSLATED}


def _renamed_sections(document: dict[str, Any]) -> dict[str, Any]:
    return {
        section: _renamed(dict(document[section] or {}), old, new)
        for section, old, new in _SECTION_RENAMES
        if section in document
    }


def upgrade_document(document: dict[str, Any], *, kind: str, experiment_id: str) -> dict[str, Any]:
    """The schema v2 document of a v1 experiment document (pure; no I/O).

    Every v1 field keeps its value under its v2 name; ``kind`` and the new id
    are the only new facts, so the translation is reversible field for field.
    """
    upgraded = _unchanged(document)
    runtime = dict(document.get("runtime") or {})
    upgraded.update(schema_version=2, kind=kind, id=experiment_id)
    upgraded["runtime"] = runtime.get("language", "python")
    if environment := _environment(runtime):
        upgraded["environment"] = environment
    upgraded["agents"] = [upgrade_agent(dict(agent)) for agent in document.get("agents") or ()]
    if "factors" in document:
        upgraded["factors"] = [FACTOR_RENAMES.get(f, f) for f in document["factors"]]
    upgraded.update(_renamed_sections(document))
    return upgraded


def infer_kind(document: dict[str, Any]) -> str:
    """The v2 kind a v1 design implies; ``smoke`` when nothing is compared."""
    factors = list(document.get("factors") or ())
    conditions = list(document.get("conditions") or ())
    if "condition" in factors and len(conditions) >= 2:
        return "ab-test"
    if factors and all(f == "agent" or f.startswith("agent.") for f in factors):
        return "benchmark"
    return "smoke"
