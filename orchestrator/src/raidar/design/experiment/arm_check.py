"""Compile every arm at ``raidar validate`` / ``raidar lock`` time.

An arm whose condition the compiler would reject (unknown or unsupported
component, invalid ``with``/files, conflicting uses, a harness with no
component mapping) must fail validation and never reach a lock, so the
rejection is visible through the public commands, not only at execution.
"""

from __future__ import annotations

from raidar.design.experiment.arm import ComponentRejectedError, compile_condition
from raidar.design.experiment.loader import LoadedExperiment
from raidar.design.experiment.validation import FieldIssue, SpecValidationError
from raidar.subject.treatment.definition.registry import ComponentRegistry


def arm_component_issues(
    loaded: LoadedExperiment, *, registry: ComponentRegistry | None = None
) -> list[FieldIssue]:
    """One issue per compiler rejection, located on the arm (``arms.<i>``)."""
    spec = loaded.spec
    registry = registry or ComponentRegistry()
    rejected: dict[tuple[str, str], tuple[str, ...]] = {}
    issues: list[FieldIssue] = []
    for index, arm in enumerate(spec.arms):
        key = (arm.condition, arm.agent)
        if key not in rejected:
            try:
                compile_condition(
                    spec.condition(arm.condition),
                    spec.agent(arm.agent),
                    base_dir=loaded.base_dir,
                    registry=registry,
                )
                rejected[key] = ()
            except ComponentRejectedError as exc:
                rejected[key] = exc.issues
        prefix = f"condition {arm.condition!r} on agent {arm.agent!r}"
        issues.extend(FieldIssue(f"arms.{index}", f"{prefix}: {text}") for text in rejected[key])
    return issues


def check_arm_components(
    loaded: LoadedExperiment, *, registry: ComponentRegistry | None = None
) -> None:
    """Raise ``SpecValidationError`` listing every arm the compiler rejects."""
    issues = arm_component_issues(loaded, registry=registry)
    if issues:
        raise SpecValidationError(str(loaded.path), issues)
