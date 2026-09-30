"""Pydantic schema for ``experiment.yaml`` (F9, F10).

Every model forbids unknown keys, so typos surface as field-level errors.
Cross-reference rules (unique ids, arm → agent/condition, baseline arm, known
policy) are checked by ``raidar.spec.validation`` so each error carries the
path of the offending field.
"""

from __future__ import annotations

import math
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, model_validator

from raidar.agents.config import Harness
from raidar.spec.references import REFERENCE_PATTERN

SCHEMA_VERSION = 1
IDENTIFIER_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"

Identifier = Annotated[str, Field(pattern=IDENTIFIER_PATTERN, max_length=64)]
Reference = Annotated[str, Field(pattern=REFERENCE_PATTERN)]
# A pinned release (``0.156.1``, ``2.1.4-beta.2``): starts with a digit, so
# floating tags (``latest``, ``stable``) and ranges (``^1.2``, ``>=1``) are refused.
PINNED_VERSION_PATTERN = r"^[0-9]+(\.[0-9]+)*([-+][0-9A-Za-z][0-9A-Za-z.-]*)?$"


def _relative_posix_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or "\\" in value:
        raise ValueError("must be a relative POSIX path (resolved against the experiment file)")
    if ".." in path.parts:
        raise ValueError("must stay inside the experiment directory ('..' is not allowed)")
    if not path.parts:
        raise ValueError("must name a file or directory below the experiment directory")
    return path.as_posix()


def _finite_json(value: JsonValue, loc: str = "") -> JsonValue:
    """Reject NaN/Infinity anywhere in a JSON value (the lock is strict JSON)."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite number {value!r} at {loc or '<value>'} is not valid JSON")
    if isinstance(value, dict):
        for key, item in value.items():
            _finite_json(item, f"{loc}.{key}" if loc else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _finite_json(item, f"{loc}.{index}" if loc else str(index))
    return value


RelativePath = Annotated[str, AfterValidator(_relative_posix_path)]
FiniteJsonObject = Annotated[dict[str, JsonValue], AfterValidator(_finite_json)]


class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ProblemEntry(SpecModel):
    """One problem: a registry reference or a local Harbor task directory."""

    ref: Reference | None = None
    path: RelativePath | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> ProblemEntry:
        if (self.ref is None) == (self.path is None):
            raise ValueError("set exactly one of 'ref' (name@version) or 'path'")
        return self

    @property
    def label(self) -> str:
        return self.ref if self.ref is not None else f"path:{self.path}"


class RuntimeSpec(SpecModel):
    language: Literal["python"] = "python"
    environment: Literal["docker"] = "docker"
    # Overhead-baseline decision: compose ``init: true`` cuts teardown 12.7s -> 2.1s p50.
    compose_init: bool = True


class AgentEntry(SpecModel):
    id: Identifier
    harness: Harness
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    reasoning_effort: str | None = None
    version: str = Field(
        pattern=PINNED_VERSION_PATTERN, description="Pinned harness CLI release (N3)"
    )


class ProtocolSpec(SpecModel):
    # Also scorer config: raidar.execution.assessment writes it for every scorer
    # that declares ``progression`` (correctness@1), so scoring follows the run.
    progression: Literal["continue", "halt_on_failure"] = "continue"
    min_reward: float | None = Field(default=None, gt=0.0, le=1.0)
    agent_timeout_sec: int = Field(default=1800, gt=0)
    verifier_timeout_sec: int = Field(default=900, gt=0)
    # Overhead-baseline decision: grade Harbor's host copy of /app by default.
    snapshot_source: Literal["artifact", "download"] = "artifact"

    @model_validator(mode="after")
    def _halt_needs_threshold(self) -> ProtocolSpec:
        if self.progression == "halt_on_failure" and self.min_reward is None:
            raise ValueError("progression 'halt_on_failure' requires min_reward")
        if self.progression == "continue" and self.min_reward is not None:
            raise ValueError("min_reward only applies to progression 'halt_on_failure'")
        return self


class ComponentUse(SpecModel):
    use: Reference
    files: tuple[RelativePath, ...] = ()
    with_: FiniteJsonObject = Field(default_factory=dict, alias="with")


class ConditionSpec(SpecModel):
    id: Identifier
    components: tuple[ComponentUse, ...] = ()


class ArmSpec(SpecModel):
    id: Identifier
    condition: Identifier
    agent: Identifier


class InventoryPolicySpec(SpecModel):
    """Assessor-owned file classification (mirrors ``raidar.scorers.inventory.InventoryPolicy``)."""

    generated_globs: tuple[str, ...] = ()
    test_globs: tuple[str, ...] = ()


class AssessmentSpec(SpecModel):
    scorers: tuple[Reference, ...] = Field(min_length=1)
    headline_policy: Reference | None = None
    inventory_policy: InventoryPolicySpec = InventoryPolicySpec()
    # Per-scorer config, keyed by a reference listed in ``scorers``.
    scorer_config: dict[Reference, FiniteJsonObject] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _config_for_declared_scorers(self) -> AssessmentSpec:
        unknown = sorted(set(self.scorer_config) - set(self.scorers))
        if unknown:
            raise ValueError(f"scorer_config names scorers not in 'scorers': {unknown}")
        return self


class AnalysisSpec(SpecModel):
    baseline_arm: Identifier
    blocks: int = Field(gt=0)
    seed: int = Field(ge=0)
    concurrency: int = Field(default=1, gt=0)


FactorNoun = Literal[
    "condition",
    "agent",
    "agent.harness",
    "agent.provider",
    "agent.model",
    "agent.reasoning_effort",
    "agent.version",
]


class ExperimentSpec(SpecModel):
    schema_version: Literal[1]
    id: Identifier
    description: str = ""
    problems: tuple[ProblemEntry, ...] = Field(min_length=1)
    runtime: RuntimeSpec = RuntimeSpec()
    agents: tuple[AgentEntry, ...] = Field(min_length=1)
    protocol: ProtocolSpec = ProtocolSpec()
    factors: tuple[FactorNoun, ...] = ()
    conditions: tuple[ConditionSpec, ...] = Field(min_length=1)
    arms: tuple[ArmSpec, ...] = Field(min_length=1)
    assessment: AssessmentSpec
    analysis: AnalysisSpec

    def agent(self, agent_id: str) -> AgentEntry:
        return next(agent for agent in self.agents if agent.id == agent_id)

    def condition(self, condition_id: str) -> ConditionSpec:
        return next(item for item in self.conditions if item.id == condition_id)

    def references(self) -> dict[str, tuple[str, ...]]:
        """Registry references by kind (policy only when declared)."""
        policy = self.assessment.headline_policy
        return {
            "problem": tuple(p.ref for p in self.problems if p.ref is not None),
            "component": tuple(
                use.use for condition in self.conditions for use in condition.components
            ),
            "scorer": self.assessment.scorers,
            "policy": (policy,) if policy is not None else (),
        }

    def to_json(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True)
