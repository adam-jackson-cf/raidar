"""Field-level validation errors and cross-reference rules for experiments.

Besides the cross-reference rules, schema v3 checks:

- release rules: every agent names a release the tree holds (``lab/releases.yaml``) and one
  of that agent's models with one of its reasoning levels; every arm's workflow release is
  the one the tree holds;
- kind rules (``KIND0``-``KIND3``): ``research-loop`` is reserved; an ``ab-test`` compares
  at least two arms; a ``benchmark`` compares agents, so every arm runs one workflow;
  ``analysis.rank_by`` is a benchmark setting;
- naming rules (``NAME1``, ``NAME2``): an experiment under
  ``experiments/<kind>/<id>/`` sits in the directory of its kind and id, and
  its id is ``<runtime>__<subject>__<intent>`` with the experiment's runtime.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from pydantic import ValidationError
from raidar_contract.references import ReleaseError

from raidar.contract.references import RegistryReferenceError
from raidar.design.experiment.agent import entry_issues
from raidar.design.experiment.schema import (
    BENCHMARK_MIN_ATTEMPTS,
    EXPERIMENT_KINDS,
    RESERVED_KINDS,
    ExperimentSpec,
    FieldIssue,
)
from raidar.scoring.catalog.builtin import (
    BUILTIN_PROTOCOL_CONFIG,
    ERRATA_CONFIG_KEY,
    SOLVABLE_CONFIG_KEY,
)
from raidar.scoring.catalog.outcomes import ALL_METRICS, OUTCOME_METRICS
from raidar.scoring.framework.metrics import DEFAULT_METRIC_REGISTRY, MetricRegistry
from raidar.subject.releases import released_agent, released_workflow

EXPERIMENTS_ROOT = "experiments"
ID_SEPARATOR = "__"


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
    arm_ids = {arm.id for arm in spec.arms}
    issues = [
        FieldIssue(f"arms.{index}.agent", f"unknown agent {arm.agent!r}")
        for index, arm in enumerate(spec.arms)
        if arm.agent not in agent_ids
    ]
    if spec.analysis.baseline_arm not in arm_ids:
        issues.append(
            FieldIssue("analysis.baseline_arm", f"unknown arm {spec.analysis.baseline_arm!r}")
        )
    return issues


def _release_issues(spec: ExperimentSpec) -> list[FieldIssue]:
    """Agent and workflow releases the tree holds, and models and reasoning they offer."""
    issues: list[FieldIssue] = []
    for index, agent in enumerate(spec.agents):
        try:
            released = released_agent(agent.release)
        except ReleaseError as exc:
            issues.append(FieldIssue(f"agents.{index}.release", str(exc)))
            continue
        issues += [
            FieldIssue(f"agents.{index}.{name}", message)
            for name, message in entry_issues(agent, released)
        ]
    checked: set[str] = set()
    for index, arm in enumerate(spec.arms):
        if arm.workflow in checked:
            continue
        checked.add(arm.workflow)
        try:
            released_workflow(arm.workflow)
        except ReleaseError as exc:
            issues.append(FieldIssue(f"arms.{index}.workflow", str(exc)))
    return issues


def _metric_issues(spec: ExperimentSpec, metrics: MetricRegistry) -> list[FieldIssue]:
    ref = spec.assessment.metric
    if ref is None:
        return []
    try:
        metrics.get(ref)
    except RegistryReferenceError as exc:
        return [FieldIssue("assessment.metric", str(exc))]
    return []


def _benchmark_issues(spec: ExperimentSpec) -> list[FieldIssue]:
    workflows = sorted({arm.workflow for arm in spec.arms})
    if len(workflows) > 1:
        return [
            FieldIssue(
                "arms",
                f"[KIND2] a benchmark compares agents: every arm runs one workflow, "
                f"not {workflows}",
            )
        ]
    return []


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
    if spec.kind == "ab-test" and len(spec.arms) < 2:
        issues.append(FieldIssue("arms", "[KIND1] an ab-test compares at least two arms"))
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
                f"{spec.runtime!r} first (for example {spec.runtime}__problem__review-loop)",
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


ProtocolConfigKeys = Mapping[str, Iterable[str]]
"""``{scorer ref: ProtocolSpec field names it takes from the protocol}``."""


def _default_protocol_config() -> ProtocolConfigKeys:
    # The scorer registry owns which scorer reads which protocol field.
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


LOCK_PINNED_CONFIG = {
    ERRATA_CONFIG_KEY: "problem errata are pinned by the problem's committed "
    "<version>.errata.yaml and reach scorers through the lock; leave it out",
    SOLVABLE_CONFIG_KEY: "the solvable sets are pinned from the problem's validation record "
    "and reach scorers through the lock; leave it out",
}


def _lock_pinned_config_issues(spec: ExperimentSpec) -> list[FieldIssue]:
    """Errata and solvable sets reach scorers through the lock only, never the experiment."""
    return [
        FieldIssue(f"assessment.scorer_config.{ref}.{key}", message)
        for ref, config in spec.assessment.scorer_config.items()
        for key, message in LOCK_PINNED_CONFIG.items()
        if key in config
    ]


def semantic_issues(
    spec: ExperimentSpec,
    metrics: MetricRegistry = DEFAULT_METRIC_REGISTRY,
    protocol_config: ProtocolConfigKeys | None = None,
    *,
    path: Path | None = None,
) -> list[FieldIssue]:
    """Cross-reference, release, kind and naming rules, each with a location."""
    return [
        *_duplicate_ids("agents", (agent.id for agent in spec.agents)),
        *_duplicate_ids("arms", (arm.id for arm in spec.arms)),
        *_duplicate_values("problems", (problem.label for problem in spec.problems)),
        *_duplicate_values("assessment.scorers", spec.assessment.scorers),
        *_reference_issues(spec),
        *_release_issues(spec),
        *_metric_issues(spec, metrics),
        *_min_effect_issues(spec),
        *_protocol_config_issues(
            spec, _default_protocol_config() if protocol_config is None else protocol_config
        ),
        *_lock_pinned_config_issues(spec),
        *kind_issues(spec),
        *name_issues(spec, path),
    ]
