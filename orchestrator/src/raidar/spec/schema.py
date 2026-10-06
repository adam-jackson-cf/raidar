"""Pydantic schema for ``experiment.yaml``, schema v2 (F9, F10).

Every model forbids unknown keys, so typos surface as field-level errors.
Cross-reference rules (unique ids, arm → agent/condition, baseline arm, known
metric), the kind rules (``KIND*``) and the naming rules (``NAME*``) are
checked by ``raidar.spec.validation`` so each error carries the path of the
offending field.

Vocabulary (Harbor's, requirements change log 2026-10-02): an experiment has
a ``kind`` (``ab-test``, ``benchmark``, ``smoke``; ``research-loop`` is
reserved), one ``runtime`` (a runtime pack id), a Harbor ``environment``
(``type: docker``), agents named by Harbor ``AgentName`` (``codex``,
``claude-code``), a headline ``metric`` and ``analysis.attempts``: the
paired, counterbalanced replicates of every arm. Schema v1 files stay
loadable read-only (``raidar.spec.schema_v1``).
"""

from __future__ import annotations

import math
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from raidar.agents.config import AgentName, Harness, agent_cli
from raidar.spec.references import REFERENCE_PATTERN

SCHEMA_VERSION = 2
IDENTIFIER_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"
RUNTIMES = ("python", "node-ts", "go", "rust", "java", "dotnet")
"""Runtime pack ids (F8). A runtime is a language toolchain; only ``python`` has a pack yet."""
EXPERIMENT_KINDS = ("ab-test", "benchmark", "smoke", "research-loop")
RESERVED_KINDS = ("research-loop",)
"""Kinds the schema accepts but no controller implements yet (``KIND0``)."""

Identifier = Annotated[str, Field(pattern=IDENTIFIER_PATTERN, max_length=64)]
Reference = Annotated[str, Field(pattern=REFERENCE_PATTERN)]
Runtime = Literal["python", "node-ts", "go", "rust", "java", "dotnet"]
ExperimentKind = Literal["ab-test", "benchmark", "smoke", "research-loop"]
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
    """One problem: a registry reference (a binding) or a local Harbor task directory."""

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


class EnvironmentSpec(SpecModel):
    """Harbor ``environment``: where trials run (``type`` is Harbor's environment type)."""

    type: Literal["docker"] = "docker"
    # Overhead-baseline decision: compose ``init: true`` cuts teardown 12.7s -> 2.1s p50.
    compose_init: bool = True


class AgentEntry(SpecModel):
    """One agent: Harbor agent ``name`` + model + kwargs (``reasoning_effort``) + version."""

    id: Identifier
    name: AgentName
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    reasoning_effort: str | None = None
    version: str = Field(pattern=PINNED_VERSION_PATTERN, description="Pinned agent CLI release")

    @property
    def harness(self) -> Harness:
        """The agent's CLI install id, as released components and the pack image key it."""
        return agent_cli(self.name)


class ProtocolSpec(SpecModel):
    # Also scorer config: raidar.execution.assessment writes it for every scorer
    # that declares ``progression`` (correctness@1), so scoring follows the job.
    progression: Literal["continue", "halt_on_failure"] = "continue"
    min_reward: float | None = Field(default=None, gt=0.0, le=1.0)
    agent_timeout_sec: int = Field(default=1800, gt=0)
    verifier_timeout_sec: int = Field(default=900, gt=0)
    # Overhead-baseline decision: grade Harbor's host copy of /app by default.
    snapshot_source: Literal["artifact", "download"] = "artifact"
    # Where the verifier's per-test coverage pass (G's evidence) runs: ``trial`` after each
    # step's grading run, on the trial's critical path; ``offline`` from the stored step
    # snapshots when an assessment needs it (raidar.verifier.coverage_offline, read by
    # code-quality@2). Left at ``trial`` it is omitted from every dump, so the locks, plans
    # and hashes of experiments that do not set it keep their bytes.
    verifier_coverage: Literal["trial", "offline"] = "trial"

    @model_serializer(mode="wrap")
    def _omit_default_coverage(self, handler: SerializerFunctionWrapHandler) -> Any:
        data = handler(self)
        if isinstance(data, dict) and self.verifier_coverage == "trial":
            data.pop("verifier_coverage", None)
        return data

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
    metric: Reference | None = None
    """The headline metric (``scb-canonical@2``); the lock picks the default by K when unset."""
    inventory_policy: InventoryPolicySpec = InventoryPolicySpec()
    # Per-scorer config, keyed by a reference listed in ``scorers``.
    scorer_config: dict[Reference, FiniteJsonObject] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _config_for_declared_scorers(self) -> AssessmentSpec:
        unknown = sorted(set(self.scorer_config) - set(self.scorers))
        if unknown:
            raise ValueError(f"scorer_config names scorers not in 'scorers': {unknown}")
        return self


DEFAULT_MIN_PAIRS = 5
DEFAULT_MAX_EXCLUSION_FRACTION = 0.2
DEFAULT_ALPHA = 0.05
BENCHMARK_MIN_ATTEMPTS = 5
"""Terminal-Bench's convention; a benchmark below it validates with a warning."""
VERDICT_THRESHOLDS = ("min_pairs", "max_exclusion_fraction", "alpha", "min_effects")
"""The ``analysis`` fields the lock pins before any data is seen (memo §2.3)."""

PositiveFiniteFloat = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]


class ManipulationCheckSpec(SpecModel):
    """One pre-registered manipulation check (``raidar.analysis.manipulation``).

    ``check`` names an available check (for example ``gate_clean_at_step_end``
    from ``gate-effort@1``); ``min_share`` is the share of the treatment arm's
    decided checkpoints that must pass. ``raidar.spec.validation`` checks the
    name and that a scorer reporting it is declared.
    """

    check: str = Field(min_length=1)
    min_share: float | None = Field(default=None, gt=0.0, le=1.0)


class AnalysisSpec(SpecModel):
    """Arm design plus the pre-registered verdict thresholds ``raidar analyze`` reads.

    ``attempts`` are Raidar's paired replicates: every attempt runs each arm
    once per problem, in a counterbalanced, seeded order, and analysis pairs
    the arms by attempt index (Harbor's ``n_attempts`` has no pairing or
    counterbalancing). ``min_effects`` maps an outcome metric id to its
    smallest effect of interest for the sample-size projection;
    ``rank_by`` (benchmarks only) names the outcome metric the ranking view
    orders agents by. ``primary_outcome`` names the pre-registered primary
    outcome the report, its markdown and the review surface label as primary
    (unset: the progression's default, ``solve_rate`` under ``continue`` and
    ``reached`` under ``halt_on_failure``); it is analysis configuration, so
    setting it relocks the ``analysis`` section only and keeps ``lock_sha256``.
    ``raidar.spec.validation`` checks every metric id. ``manipulation_checks``
    pre-registers the manipulation checks (and any threshold) analysis runs;
    unset, analysis runs the process-compliance defaults when that scorer is
    declared.
    """

    baseline_arm: Identifier
    attempts: int = Field(gt=0)
    seed: int = Field(ge=0)
    concurrency: int = Field(default=1, gt=0)
    min_pairs: int = Field(default=DEFAULT_MIN_PAIRS, ge=2)
    max_exclusion_fraction: float = Field(default=DEFAULT_MAX_EXCLUSION_FRACTION, ge=0.0, le=1.0)
    alpha: float = Field(default=DEFAULT_ALPHA, gt=0.0, lt=1.0)
    min_effects: dict[str, PositiveFiniteFloat] = Field(default_factory=dict)
    rank_by: str | None = None
    primary_outcome: str | None = None
    manipulation_checks: tuple[ManipulationCheckSpec, ...] | None = Field(
        default=None, min_length=1
    )

    @model_validator(mode="after")
    def _unique_manipulation_checks(self) -> AnalysisSpec:
        names = [item.check for item in self.manipulation_checks or ()]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"manipulation_checks repeats {duplicates}")
        return self


FactorNoun = Literal[
    "condition",
    "agent",
    "agent.name",
    "agent.provider",
    "agent.model",
    "agent.reasoning_effort",
    "agent.version",
]


class ExperimentSpec(SpecModel):
    schema_version: Literal[2]
    kind: ExperimentKind
    id: Identifier
    description: str = ""
    problems: tuple[ProblemEntry, ...] = Field(min_length=1)
    runtime: Runtime
    environment: EnvironmentSpec = EnvironmentSpec()
    agents: tuple[AgentEntry, ...] = Field(min_length=1)
    protocol: ProtocolSpec = ProtocolSpec()
    factors: tuple[FactorNoun, ...] = ()
    # Applied identically to every condition, ahead of its own components: framing
    # every arm shares (e.g. a prompt-wrapper) without being part of any factor.
    shared_components: tuple[ComponentUse, ...] = ()
    conditions: tuple[ConditionSpec, ...] = Field(min_length=1)
    arms: tuple[ArmSpec, ...] = Field(min_length=1)
    assessment: AssessmentSpec
    analysis: AnalysisSpec

    def agent(self, agent_id: str) -> AgentEntry:
        return next(agent for agent in self.agents if agent.id == agent_id)

    def condition(self, condition_id: str) -> ConditionSpec:
        """The effective condition: ``shared_components`` first, then its own components."""
        declared = next(item for item in self.conditions if item.id == condition_id)
        if not self.shared_components:
            return declared
        return declared.model_copy(
            update={"components": (*self.shared_components, *declared.components)}
        )

    def component_uses(self) -> list[tuple[str, ComponentUse]]:
        """Every declared component use with its field location (shared ones once)."""
        shared = [
            (f"shared_components.{index}", use) for index, use in enumerate(self.shared_components)
        ]
        own = [
            (f"conditions.{c_index}.components.{u_index}", use)
            for c_index, condition in enumerate(self.conditions)
            for u_index, use in enumerate(condition.components)
        ]
        return [*shared, *own]

    def references(self) -> dict[str, tuple[str, ...]]:
        """Registry references by kind (metric only when declared)."""
        metric = self.assessment.metric
        return {
            "problem": tuple(p.ref for p in self.problems if p.ref is not None),
            "component": tuple(use.use for _, use in self.component_uses()),
            "scorer": self.assessment.scorers,
            "metric": (metric,) if metric is not None else (),
        }

    def to_json(self) -> dict[str, object]:
        # Optional fields left unset are omitted, as schema v1 omitted them, so the
        # execution section of a migrated v1 lock compares field for field.
        exclude: dict[str, object] = {}
        if not self.shared_components:
            exclude["shared_components"] = True
        unset = {
            name
            for name in ("rank_by", "primary_outcome", "manipulation_checks")
            if getattr(self.analysis, name) is None
        }
        if unset:
            exclude["analysis"] = unset
        return self.model_dump(mode="json", by_alias=True, exclude=exclude or None)
