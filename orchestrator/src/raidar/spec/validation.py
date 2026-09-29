"""Field-level validation errors and cross-reference rules for experiments."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from raidar.spec.agent_support import agent_support_issues
from raidar.spec.factor_guard import check_factors
from raidar.spec.policies import DEFAULT_POLICY_REGISTRY, ScorePolicyRegistry
from raidar.spec.references import RegistryReferenceError
from raidar.spec.schema import ExperimentSpec


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
    for c_index, condition in enumerate(spec.conditions):
        for u_index, component in enumerate(condition.components):
            for f_index, relative in enumerate(component.files):
                owner = next((task for task in task_dirs if _path_owner(relative, task)), None)
                if owner is not None:
                    issues.append(
                        FieldIssue(
                            f"conditions.{c_index}.components.{u_index}.files.{f_index}",
                            f"overlaps local problem task directory {owner!r}; component "
                            "files may not include task or hidden test files",
                        )
                    )
    return issues


def _policy_issues(spec: ExperimentSpec, policies: ScorePolicyRegistry) -> list[FieldIssue]:
    ref = spec.assessment.headline_policy
    if ref is None:
        return []
    try:
        policies.get(ref)
    except RegistryReferenceError as exc:
        return [FieldIssue("assessment.headline_policy", str(exc))]
    return []


def semantic_issues(
    spec: ExperimentSpec, policies: ScorePolicyRegistry = DEFAULT_POLICY_REGISTRY
) -> list[FieldIssue]:
    """Cross-reference rules plus the factor guard, each with a field location."""
    issues = [
        *_duplicate_ids("agents", (agent.id for agent in spec.agents)),
        *_duplicate_ids("conditions", (condition.id for condition in spec.conditions)),
        *_duplicate_ids("arms", (arm.id for arm in spec.arms)),
        *_duplicate_values("problems", (problem.label for problem in spec.problems)),
        *_duplicate_values("assessment.scorers", spec.assessment.scorers),
        *_duplicate_values("factors", spec.factors),
        *_reference_issues(spec),
        *_agent_issues(spec),
        *_component_overlay_issues(spec),
        *_policy_issues(spec, policies),
    ]
    if issues:
        # The factor guard needs resolvable arm references and unique ids.
        return issues
    return [FieldIssue(item.loc, item.message) for item in check_factors(spec)]
