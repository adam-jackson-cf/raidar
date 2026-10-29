"""Pydantic schema for ``experiment.yaml``, schema v3.

Every model forbids unknown keys, so typos surface as field-level errors.
Cross-reference rules (unique ids, arm -> agent, baseline arm, known metric), the
release rules (agent release, model and reasoning), the kind rules (``KIND*``) and the
naming rules (``NAME*``) are checked by ``raidar.design.experiment.validation`` so each
error carries the path of the offending field.

Vocabulary (Harbor's): an experiment has a ``kind`` (``ab-test``, ``benchmark``,
``smoke``; ``research-loop`` is reserved), one ``runtime`` (a runtime pack id), a Harbor
``environment`` (``type: docker``), a headline ``metric`` and ``analysis.attempts``: the
paired, counterbalanced replicates of every arm. An **arm** is an agent plus a workflow:
``agents`` name an agent release (``codex@3``, ``lab/agents/``) with one of its models and
that model's reasoning level; ``arms`` name an agent id and a workflow release
(``lab/workflows/``), ``one-shot@1`` when omitted.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
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

from raidar.contract.references import REFERENCE_PATTERN
from raidar.scoring.catalog.outcomes import declared_outcome_types

DEFAULT_WORKFLOW = "one-shot@1"
"""The workflow an arm runs when it names none (design section 2)."""

IDENTIFIER_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"
EXPERIMENT_KINDS = ("ab-test", "benchmark", "smoke", "research-loop")
RESERVED_KINDS = ("research-loop",)
"""Kinds the schema accepts but no controller implements yet (``KIND0``)."""

Identifier = Annotated[str, Field(pattern=IDENTIFIER_PATTERN, max_length=64)]
Reference = Annotated[str, Field(pattern=REFERENCE_PATTERN)]
Runtime = Literal["python", "node-ts", "go", "rust", "java", "dotnet"]
"""Runtime pack ids (F8). A runtime is a language toolchain; only ``python`` has a pack yet."""
ExperimentKind = Literal["ab-test", "benchmark", "smoke", "research-loop"]
Variation = Literal["harness", "model", "reasoning"]
"""A factor an experiment may declare it varies beyond the arm's agent entry (``varies``)."""
# A pinned release (``0.156.1``, ``2.1.4-beta.2``): starts with a digit, so
# floating tags (``latest``, ``stable``) and ranges (``^1.2``, ``>=1``) are refused.
PINNED_VERSION_PATTERN = r"^[0-9]+(\.[0-9]+)*([-+][0-9A-Za-z][0-9A-Za-z.-]*)?$"


@dataclass(frozen=True, order=True)
class FieldIssue:
    """One problem, located by a dotted path into the experiment document."""

    loc: str
    message: str

    def render(self) -> str:
        return f"{self.loc or '<root>'}: {self.message}"


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
    """One problem: a registry reference (a binding) or a local Harbor task directory.

    ``checkpoints: k`` binds the first ``k`` checkpoints of a multi-checkpoint reference
    (``scb/<problem>@<version>``) as one multi-step task; unset runs every checkpoint.
    """

    ref: Reference | None = None
    path: RelativePath | None = None
    checkpoints: Annotated[int, Field(strict=True, ge=1)] | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> ProblemEntry:
        if (self.ref is None) == (self.path is None):
            raise ValueError("set exactly one of 'ref' (name@version) or 'path'")
        if self.checkpoints is not None and self.ref is None:
            raise ValueError("'checkpoints' selects within a registry 'ref', not a local 'path'")
        return self

    @property
    def label(self) -> str:
        base = self.ref if self.ref is not None else f"path:{self.path}"
        return base if self.checkpoints is None else f"{base}[:{self.checkpoints}]"


class EnvironmentSpec(SpecModel):
    """Harbor ``environment``: where trials run (``type`` is Harbor's environment type)."""

    type: Literal["docker"] = "docker"
    # Overhead-baseline decision: compose ``init: true`` cuts teardown 12.7s -> 2.1s p50.
    compose_init: bool = True


class AgentEntry(SpecModel):
    """One agent: an agent release, one of its models and that model's reasoning level."""

    id: Identifier
    release: Reference
    model: str = Field(min_length=1)
    reasoning: str | None = None


class ProtocolSpec(SpecModel):
    # Also scorer config: raidar.engine.job.assessment writes it for every scorer
    # that declares ``progression`` (correctness@9), so scoring follows the job.
    progression: Literal["continue", "halt_on_failure"] = "continue"
    min_reward: float | None = Field(default=None, gt=0.0, le=1.0)
    agent_timeout_sec: int = Field(default=1800, gt=0)
    verifier_timeout_sec: int = Field(default=900, gt=0)
    # Overhead-baseline decision: grade Harbor's host copy of /app by default.
    snapshot_source: Literal["artifact", "download"] = "artifact"
    # Where the verifier's per-test coverage pass (G's evidence) runs: ``trial`` after each
    # step's grading run, on the trial's critical path; ``offline`` from the stored step
    # snapshots when an assessment needs it (raidar.grading.coverage.offline, read by
    # code-quality@8). Left at ``trial`` it is omitted from every dump, so the locks, plans
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


class ArmSpec(SpecModel):
    """One arm: an agent (by id) and the workflow release it runs."""

    id: Identifier
    agent: Identifier
    workflow: Reference = DEFAULT_WORKFLOW


class InventoryPolicySpec(SpecModel):
    """Assessor-owned file classification (mirrors ``scoring.framework.inventory``)."""

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
EXECUTION_ANALYSIS_FIELDS = ("attempts", "seed")
"""The analysis fields that shape the plan; ``concurrency`` is per machine (PlanOptions)."""

PositiveFiniteFloat = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]


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
    (unset: the solvable-set solve rate from
    ``raidar.scoring.catalog.outcomes.primary_metric``; an errata-applied outcome is a
    pre-registered choice, never the default); it is analysis configuration, so
    setting it relocks the ``analysis`` section only and keeps ``lock_sha256``.
    ``raidar.design.experiment.validation`` checks every metric id.
    """

    baseline_arm: Identifier
    attempts: int = Field(gt=0)
    seed: int = Field(ge=0)
    concurrency: int = Field(default=1, gt=0)
    min_pairs: int = Field(default=DEFAULT_MIN_PAIRS, ge=2)
    max_exclusion_fraction: float = Field(default=DEFAULT_MAX_EXCLUSION_FRACTION, ge=0.0, le=1.0)
    alpha: float = Field(default=DEFAULT_ALPHA, gt=0.0, lt=1.0)
    max_exclusion_difference: float | None = Field(default=None, ge=0.0, le=1.0)
    """The largest difference in per-arm exclusion rates the experiment accepts, pre-registered.
    Unset, analysis refuses every comparative verdict (a non-significant difference never
    establishes acceptable attrition); Raidar supplies no default value."""
    min_effects: dict[str, PositiveFiniteFloat] = Field(default_factory=dict)
    rank_by: str | None = None
    primary_outcome: str | None = None


class ExperimentSpec(SpecModel):
    schema_version: Literal[3]
    kind: ExperimentKind
    id: Identifier
    description: str = ""
    problems: tuple[ProblemEntry, ...] = Field(min_length=1)
    runtime: Runtime
    environment: EnvironmentSpec = EnvironmentSpec()
    agents: tuple[AgentEntry, ...] = Field(min_length=1)
    protocol: ProtocolSpec = ProtocolSpec()
    arms: tuple[ArmSpec, ...] = Field(min_length=1)
    varies: tuple[Variation, ...] = ()
    """The factors the arms differ in on top of the agent entry, declared so admission and
    pooling accept it. ``harness``: the arms' agent releases declare different capability
    manifests (every arm must still declare all nine); the arms then must share the canonical
    model id and reasoning, unless ``model`` / ``reasoning`` is declared too (those are only
    meaningful with ``harness``)."""
    assessment: AssessmentSpec
    analysis: AnalysisSpec

    @model_validator(mode="after")
    def _variation_declared_once(self) -> ExperimentSpec:
        if len(set(self.varies)) != len(self.varies):
            raise ValueError("varies lists a factor more than once")
        if self.varies and "harness" not in self.varies:
            raise ValueError("varies: model and reasoning variation is declared with harness")
        return self

    def agent(self, agent_id: str) -> AgentEntry:
        return next(agent for agent in self.agents if agent.id == agent_id)

    def arm(self, arm_id: str) -> ArmSpec:
        return next(arm for arm in self.arms if arm.id == arm_id)

    def references(self) -> dict[str, tuple[str, ...]]:
        """Registry references by kind (metric only when declared)."""
        metric = self.assessment.metric
        return {
            "problem": tuple(p.ref for p in self.problems if p.ref is not None),
            "agent": tuple(dict.fromkeys(agent.release for agent in self.agents)),
            "workflow": tuple(dict.fromkeys(arm.workflow for arm in self.arms)),
            "scorer": self.assessment.scorers,
            "metric": (metric,) if metric is not None else (),
        }

    def to_json(self) -> dict[str, object]:
        # Optional fields left unset are omitted, so a spec that does not set them keeps
        # its bytes (and so its lock digests) as they were before the field existed.
        unset = {
            name for name in ("rank_by", "primary_outcome") if getattr(self.analysis, name) is None
        }
        exclude: dict[str, Any] = {"analysis": unset} if unset else {}
        if not self.varies:
            exclude["varies"] = True
        dump = self.model_dump(mode="json", by_alias=True, exclude=exclude or None)
        # An unset ``checkpoints`` is omitted too: bindings without it keep their lock bytes.
        dump["problems"] = [
            {k: v for k, v in problem.items() if k != "checkpoints" or v is not None}
            for problem in dump["problems"]
        ]
        return dump


def analysis_section(
    spec_json: Mapping[str, Any], checkpoints: Mapping[str, int]
) -> dict[str, object]:
    """Baseline arm, verdict thresholds and declared outcome types.

    ``spec.analysis`` minus the plan-shaping fields, plus ``outcome_types``: per problem the
    bound checkpoint count and the outcomes declared binary there, and the aggregate's
    declaration (``declared_outcome_types``). Analysis picks its exact test from these
    declarations, never from observed values.
    """
    excluded = {*EXECUTION_ANALYSIS_FIELDS, "concurrency"}
    kept = {key: value for key, value in spec_json["analysis"].items() if key not in excluded}
    return {**kept, "outcome_types": declared_outcome_types(checkpoints)}
