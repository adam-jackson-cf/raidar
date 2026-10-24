"""Field-level validation errors and cross-reference rules for experiments.

Besides the cross-reference rules and the factor guard, schema v2 adds:

- kind rules (``KIND0``-``KIND3``): ``research-loop`` is reserved; an
  ``ab-test`` compares conditions; a ``benchmark`` compares agents under one
  condition; ``analysis.rank_by`` is a benchmark setting;
- naming rules (``NAME1``, ``NAME2``): an experiment under
  ``experiments/<kind>/<id>/`` sits in the directory of its kind and id, and
  its id is ``<runtime>__<subject>__<intent>`` with the experiment's runtime.

They forbid contradictions only; designs the factor guard allows stay allowed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from raidar.contract.references import RegistryReferenceError
from raidar.design.experiment.agent_support import agent_support_issues
from raidar.design.experiment.factor_guard import check_factors
from raidar.design.experiment.metrics import DEFAULT_METRIC_REGISTRY, MetricRegistry
from raidar.design.experiment.schema import (
    BENCHMARK_MIN_ATTEMPTS,
    EXPERIMENT_KINDS,
    RESERVED_KINDS,
    ExperimentSpec,
)

EXPERIMENTS_ROOT = "experiments"
ID_SEPARATOR = "__"


@dataclass(frozen=True, order=True)
class FieldIssue:
    """One problem, located by a dotted path into the experiment document."""

    loc: str
    message: str

    def render(self) -> str:
        return f"{self.loc or '<root>'}: {self.message}"


class SpecValidationError(Exception):
    """The experiment is invalid; ``issues`` lists every field-level error."""

    def __init__(self, source: str, issues: Sequence[FieldIssue]) -> None:
        self.source = source
        self.issues = tuple(issues)
        lines = "\n".join(f"  {issue.render()}" for issue in self.issues)
        super().__init__(f"{source}: {len(self.issues)} validation error(s)\n{lines}")


def issues_from_pydantic(error: ValidationError) -> list[FieldIssue]:
    return [
        FieldIssue(loc=".".join(str(part) for part in item["loc"]), message=item["msg"])
        for item in error.errors()
    ]


def _duplicate_ids(section: str, ids: Iterable[str]) -> list[FieldIssue]:
    seen: set[str] = set()
    issues: list[FieldIssue] = []
    for index, item_id in enumerate(ids):
        if item_id in seen:
            issues.append(FieldIssue(f"{section}.{index}.id", f"duplicate id {item_id!r}"))
        seen.add(item_id)
    return issues


def _duplicate_values(section: str, values: Iterable[str]) -> list[FieldIssue]:
    seen: set[str] = set()
    issues: list[FieldIssue] = []
    for index, value in enumerate(values):
        if value in seen:
            issues.append(FieldIssue(f"{section}.{index}", f"duplicate entry {value!r}"))
        seen.add(value)
    return issues


def _reference_issues(spec: ExperimentSpec) -> list[FieldIssue]:
    agent_ids = {agent.id for agent in spec.agents}
    condition_ids = {condition.id for condition in spec.conditions}
    arm_ids = {arm.id for arm in spec.arms}
    issues: list[FieldIssue] = []
    for index, arm in enumerate(spec.arms):
        if arm.agent not in agent_ids:
            issues.append(FieldIssue(f"arms.{index}.agent", f"unknown agent {arm.agent!r}"))
        if arm.condition not in condition_ids:
            issues.append(
                FieldIssue(f"arms.{index}.condition", f"unknown condition {arm.condition!r}")
            )
    if spec.analysis.baseline_arm not in arm_ids:
        issues.append(
            FieldIssue("analysis.baseline_arm", f"unknown arm {spec.analysis.baseline_arm!r}")
        )
    return issues


def _agent_issues(spec: ExperimentSpec) -> list[FieldIssue]:
    return [
        FieldIssue(loc, message)
        for index, agent in enumerate(spec.agents)
        for loc, message in agent_support_issues(index, agent)
    ]


def _path_owner(path: str, owner: str) -> bool:
    return path == owner or path.startswith(f"{owner}/") or owner.startswith(f"{path}/")


def _component_overlay_issues(spec: ExperimentSpec) -> list[FieldIssue]:
    """Component files may not overlap a local problem's task directory (F4).

    Overlaying task files (including hidden ``tests/``) into the agent's
    workspace would leak the verifier, so the overlap is refused at the spec.
    """
    task_dirs = [problem.path for problem in spec.problems if problem.path is not None]
    issues: list[FieldIssue] = []
    for loc, component in spec.component_uses():
        for f_index, relative in enumerate(component.files):
            owner = next((task for task in task_dirs if _path_owner(relative, task)), None)
            if owner is not None:
                issues.append(
                    FieldIssue(
                        f"{loc}.files.{f_index}",
                        f"overlaps local problem task directory {owner!r}; component "
                        "files may not include task or hidden test files",
                    )
                )
    return issues


def _shared_component_issues(spec: ExperimentSpec) -> list[FieldIssue]:
    """A condition may not repeat a shared component: the arms would not share it alone."""
    shared = {use.use for use in spec.shared_components}
    return [
        FieldIssue(
            f"conditions.{c_index}.components.{u_index}.use",
            f"{use.use} is already in shared_components, which every condition gets",
        )
        for c_index, condition in enumerate(spec.conditions)
        for u_index, use in enumerate(condition.components)
        if use.use in shared
    ]


def _metric_issues(spec: ExperimentSpec, metrics: MetricRegistry) -> list[FieldIssue]:
    ref = spec.assessment.metric
    if ref is None:
        return []
    try:
        metrics.get(ref)
    except RegistryReferenceError as exc:
        return [FieldIssue("assessment.metric", str(exc))]
    return []


def _agent_factor(factor: str) -> bool:
    return factor == "agent" or factor.startswith("agent.")


def _ab_test_issues(spec: ExperimentSpec) -> list[FieldIssue]:
    issues = []
    if "condition" not in spec.factors:
        issues.append(
            FieldIssue(
                "factors",
                "[KIND1] an ab-test compares conditions: factors must include 'condition' "
                "(agent.* factors may be added)",
            )
        )
    if len(spec.conditions) < 2:
        issues.append(FieldIssue("conditions", "[KIND1] an ab-test needs at least two conditions"))
    return issues


def _benchmark_issues(spec: ExperimentSpec) -> list[FieldIssue]:
    issues = []
    if not spec.factors or not all(_agent_factor(factor) for factor in spec.factors):
        issues.append(
            FieldIssue(
                "factors",
                "[KIND2] a benchmark compares agents: factors must be a non-empty set of "
                "agent factors (agent, agent.name, agent.model, ...), never 'condition'",
            )
        )
    if len(spec.conditions) != 1:
        issues.append(
            FieldIssue(
                "conditions",
                "[KIND2] a benchmark runs every agent under exactly one condition "
                "(put shared framing in shared_components)",
            )
        )
    return issues


def kind_issues(spec: ExperimentSpec) -> list[FieldIssue]:
    """``KIND*``: the design must not contradict the declared kind."""
    if spec.kind in RESERVED_KINDS:
        return [
            FieldIssue(
                "kind",
                f"[KIND0] kind {spec.kind!r} is reserved for the research-loop controller, "
                "which is not implemented yet",
            )
        ]
    issues = []
    if spec.kind == "ab-test":
        issues.extend(_ab_test_issues(spec))
    elif spec.kind == "benchmark":
        issues.extend(_benchmark_issues(spec))
    if spec.analysis.rank_by is not None and spec.kind != "benchmark":
        issues.append(
            FieldIssue(
                "analysis.rank_by",
                f"[KIND3] rank_by orders a benchmark's ranking view; kind {spec.kind!r} has none",
            )
        )
    return issues


EXPERIMENT_FILE = "experiment.yaml"


def _experiments_relative(path: Path) -> tuple[str, ...] | None:
    """The path's parts below its nearest ``experiments/`` ancestor, or None outside one."""
    parts = path.parts
    for index in range(len(parts) - 2, -1, -1):
        if parts[index] == EXPERIMENTS_ROOT:
            return parts[index + 1 :]
    return None


def _layout_issues(spec: ExperimentSpec, relative: tuple[str, ...]) -> list[FieldIssue]:
    """``NAME1`` for a file below an ``experiments/`` directory."""
    if len(relative) != 3 or relative[-1] != EXPERIMENT_FILE:
        return [
            FieldIssue(
                "id",
                f"[NAME1] the file sits at experiments/{'/'.join(relative)}; an experiment under "
                f"experiments/ must be experiments/<kind>/<id>/{EXPERIMENT_FILE} "
                f"(here experiments/{spec.kind}/{spec.id}/{EXPERIMENT_FILE})",
            )
        ]
    kind_dir, id_dir, _ = relative
    issues = []
    if kind_dir != spec.kind:
        known = ", ".join(EXPERIMENT_KINDS)
        issues.append(
            FieldIssue(
                "kind",
                f"[NAME1] the file sits under experiments/{kind_dir}/ but declares kind "
                f"{spec.kind!r}; move it to experiments/{spec.kind}/ (kinds: {known})",
            )
        )
    if id_dir != spec.id:
        issues.append(
            FieldIssue(
                "id",
                f"[NAME1] the file sits in experiments/{kind_dir}/{id_dir}/ but declares id "
                f"{spec.id!r}; the directory must be named after the id",
            )
        )
    return issues


def name_issues(spec: ExperimentSpec, path: Path | None) -> list[FieldIssue]:
    """``NAME1`` (layout) and ``NAME2`` (id grammar ``<runtime>__<subject>__<intent>``).

    ``NAME1`` applies to any file below an ``experiments/`` directory, at any
    depth: it must be ``experiments/<kind>/<id>/experiment.yaml``. A copy outside
    every ``experiments/`` directory (a scratch lock input) is checked by
    ``NAME2`` only.
    """
    relative = _experiments_relative(path) if path is not None else None
    issues = _layout_issues(spec, relative) if relative is not None else []
    segments = spec.id.split(ID_SEPARATOR)
    if len(segments) != 3 or not all(segments) or segments[0] != spec.runtime:
        issues.append(
            FieldIssue(
                "id",
                f"[NAME2] id {spec.id!r} must be <runtime>__<subject>__<intent> with runtime "
                f"{spec.runtime!r} first (for example {spec.runtime}__forge__review-loop)",
            )
        )
    return issues


def spec_warnings(spec: ExperimentSpec) -> list[FieldIssue]:
    """Advice that does not block validation (printed by validate, lock and plan)."""
    warnings = []
    if spec.kind == "benchmark" and spec.analysis.attempts < BENCHMARK_MIN_ATTEMPTS:
        warnings.append(
            FieldIssue(
                "analysis.attempts",
                f"a benchmark with {spec.analysis.attempts} attempt(s) is below the "
                f"{BENCHMARK_MIN_ATTEMPTS} attempts per agent Terminal-Bench reports with",
            )
        )
    if spec.kind == "smoke":
        warnings.append(
            FieldIssue("kind", "a smoke experiment checks operational health; it is not citable")
        )
    return warnings


def _min_effect_issues(spec: ExperimentSpec) -> list[FieldIssue]:
    # Analysis owns the outcome metric ids; imported lazily like the scorer catalog.
    from raidar.evaluation.analysis.outcomes import ALL_METRICS, OUTCOME_METRICS

    known = {metric.id for metric in ALL_METRICS}
    issues = [
        FieldIssue(
            f"analysis.min_effects.{name}",
            f"unknown outcome metric {name!r}; known: {', '.join(sorted(known))}",
        )
        for name in spec.analysis.min_effects
        if name not in known
    ]
    rank_by = spec.analysis.rank_by
    if rank_by is not None and rank_by not in known:
        issues.append(
            FieldIssue(
                "analysis.rank_by",
                f"unknown outcome metric {rank_by!r}; known: {', '.join(sorted(known))}",
            )
        )
    # The primary is a trial outcome every arm has (gate effort describes one treatment).
    outcomes = {metric.id for metric in OUTCOME_METRICS}
    primary = spec.analysis.primary_outcome
    if primary is not None and primary not in outcomes:
        issues.append(
            FieldIssue(
                "analysis.primary_outcome",
                f"unknown primary outcome {primary!r}; known: {', '.join(sorted(outcomes))}",
            )
        )
    return issues


def _manipulation_check_issues(spec: ExperimentSpec) -> list[FieldIssue]:
    """A pre-registered check must exist and a declared scorer must report it."""
    # Analysis owns the checks and the scorers they read; imported lazily.
    from raidar.evaluation.analysis.manipulation import CHECK_SCORERS

    declared = set(spec.assessment.scorers)
    issues = []
    for index, item in enumerate(spec.analysis.manipulation_checks or ()):
        where = f"analysis.manipulation_checks.{index}.check"
        if item.check not in CHECK_SCORERS:
            known = ", ".join(sorted(CHECK_SCORERS))
            issues.append(FieldIssue(where, f"unknown check {item.check!r}; known: {known}"))
            continue
        refs = CHECK_SCORERS[item.check][1]
        if not declared & set(refs):
            issues.append(
                FieldIssue(
                    where,
                    f"{item.check} is reported by {' or '.join(refs)}; declare it in "
                    "assessment.scorers",
                )
            )
    return issues


ProtocolConfigKeys = Mapping[str, Iterable[str]]
"""``{scorer ref: ProtocolSpec field names it takes from the protocol}``."""


def _default_protocol_config() -> ProtocolConfigKeys:
    # The scorer registry owns which scorer reads which protocol field.
    from raidar.scoring.framework.catalog import BUILTIN_PROTOCOL_CONFIG

    return BUILTIN_PROTOCOL_CONFIG


def _protocol_config_issues(
    spec: ExperimentSpec, protocol_config: ProtocolConfigKeys
) -> list[FieldIssue]:
    """A scorer takes its protocol keys from the protocol; a contradicting config is refused."""
    protocol = spec.protocol.model_dump(mode="json")
    return [
        FieldIssue(
            f"assessment.scorer_config.{ref}.{name}",
            f"contradicts protocol.{name} {protocol[name]!r}; {ref} takes it from the "
            "protocol, so leave it out",
        )
        for ref, config in spec.assessment.scorer_config.items()
        for name in protocol_config.get(ref, ())
        if name in config and config[name] != protocol[name]
    ]


ERRATA_CONFIG_KEY = "errata"


def _errata_config_issues(spec: ExperimentSpec) -> list[FieldIssue]:
    """Problem errata come from the problem's committed errata file through the lock only."""
    return [
        FieldIssue(
            f"assessment.scorer_config.{ref}.{ERRATA_CONFIG_KEY}",
            "problem errata are pinned by the problem's committed <version>.errata.yaml and "
            "reach scorers through the lock; leave it out",
        )
        for ref, config in spec.assessment.scorer_config.items()
        if ERRATA_CONFIG_KEY in config
    ]


def semantic_issues(
    spec: ExperimentSpec,
    metrics: MetricRegistry = DEFAULT_METRIC_REGISTRY,
    protocol_config: ProtocolConfigKeys | None = None,
    *,
    path: Path | None = None,
) -> list[FieldIssue]:
    """Cross-reference, kind and naming rules plus the factor guard, each with a location."""
    structural = [
        *_duplicate_ids("agents", (agent.id for agent in spec.agents)),
        *_duplicate_ids("conditions", (condition.id for condition in spec.conditions)),
        *_duplicate_ids("arms", (arm.id for arm in spec.arms)),
        *_duplicate_values("problems", (problem.label for problem in spec.problems)),
        *_duplicate_values("assessment.scorers", spec.assessment.scorers),
        *_duplicate_values("factors", spec.factors),
        *_reference_issues(spec),
        *_agent_issues(spec),
        *_component_overlay_issues(spec),
        *_shared_component_issues(spec),
        *_metric_issues(spec, metrics),
        *_min_effect_issues(spec),
        *_manipulation_check_issues(spec),
        *_protocol_config_issues(
            spec, _default_protocol_config() if protocol_config is None else protocol_config
        ),
        *_errata_config_issues(spec),
    ]
    labels = [*kind_issues(spec), *name_issues(spec, path)]
    if structural:
        # The factor guard needs resolvable arm references and unique ids.
        return [*structural, *labels]
    return [*labels, *(FieldIssue(item.loc, item.message) for item in check_factors(spec))]
