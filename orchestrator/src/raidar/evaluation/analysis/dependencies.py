"""Per-arm dependency readings: the share of steps whose graded submission lacked a package.

Each attempted step carries two health readings (``raidar.engine.health.dependencies``):
``unprovided`` (a declared requirement Raidar could not provide) and ``undeclared_import``
(a ``ModuleNotFoundError`` for an undeclared non-stdlib module). An arm's share is affected
steps over *measured* steps; a step whose evidence is missing is counted as unmeasured, never
as unaffected, and an arm with no measured step has no share (``None``), never 0. Beside the
share, the report gives how many affected steps failed their solvable set, where the correctness
Assessment has that verdict. Readings describe the instrument and change no outcome or validity.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from raidar.engine.health.model import HealthReport, load_health
from raidar.evaluation.analysis.assessed import CORRECTNESS_REF, SOLVABLE_SOLVED, Observed
from raidar.evaluation.analysis.outcomes import AnalysisModel, DependencyStep, TrialOutcome
from raidar.scoring.framework.result import AssessResult

UNPROVIDED = "unprovided"
UNDECLARED_IMPORT = "undeclared_import"
READINGS = (UNPROVIDED, UNDECLARED_IMPORT)
MATERIAL_SHARE_DIFFERENCE = 0.10
"""Absolute difference between two arms' shares of affected steps that the report warns about."""

_CHECK_IDS = {UNPROVIDED: "D1", UNDECLARED_IMPORT: "D2"}


class ReadingShare(AnalysisModel):
    """One reading over an arm's valid trials' attempted steps."""

    measured: int
    affected: int
    unmeasured: int
    share: float | None
    """``affected / measured``; ``None`` when no step was measured."""
    solvable_failed: int
    """Affected steps whose solvable set did not pass."""
    solvable_judged: int
    """Affected steps that have a solvable-set verdict (the denominator of ``solvable_failed``)."""
    heuristic: int = 0
    """Affected steps whose findings rest on a name match alone (``undeclared_import`` only):
    the module may be an import alias of a declared distribution."""


def _evidence(report: HealthReport | None, check_id: str) -> tuple[dict[str, object], set[str]]:
    """The measured steps' values and the steps the check names as unmeasured."""
    check = next((c for c in report.checks if c.id == check_id), None) if report else None
    if check is None:
        return {}, set()
    steps = check.evidence.get("steps")
    unmeasured = check.evidence.get("unmeasured")
    return (
        dict(steps) if isinstance(steps, dict) else {},
        {str(s) for s in unmeasured} if isinstance(unmeasured, list) else set(),
    )


def _solvable(correctness: Observed, step: str) -> bool | None:
    value = correctness.value(SOLVABLE_SOLVED, step)
    return None if value.value is None else bool(value.value)


def _unprovided(value: object) -> tuple[dict[str, str], ...] | None:
    if not isinstance(value, list):
        return None
    return tuple(
        {"requirement": str(item.get("requirement")), "reason": str(item.get("reason"))}
        for item in value
        if isinstance(item, dict)
    )


def _imports(value: object) -> tuple[dict[str, str], ...] | None:
    if not isinstance(value, list):
        return None
    return tuple(
        {"module": str(item.get("module")), "basis": str(item.get("basis"))}
        for item in value
        if isinstance(item, dict)
    )


def dependency_steps(
    steps: Sequence[str], trial_dir: Path, correctness: AssessResult | None
) -> tuple[DependencyStep, ...]:
    """The trial's per-step readings from its ``health.json``.

    A trial whose audit carries neither reading (no audit, or one written before ``D1``/``D2``)
    has every planned step unmeasured, never unaffected. Otherwise the steps the audit attempted
    appear (a step the run never reached has no reading to miss).
    """
    report = load_health(trial_dir)
    unprovided, missing_d1 = _evidence(report, _CHECK_IDS[UNPROVIDED])
    imports, missing_d2 = _evidence(report, _CHECK_IDS[UNDECLARED_IMPORT])
    named = set(unprovided) | set(imports) | missing_d1 | missing_d2
    has_readings = report is not None and any(c.id in _CHECK_IDS.values() for c in report.checks)
    attempted = [s for s in steps if s in named] if has_readings else list(steps)
    observed = Observed(CORRECTNESS_REF, correctness)
    return tuple(
        DependencyStep(
            step=step,
            unprovided=_unprovided(unprovided.get(step)),
            undeclared_imports=_imports(imports.get(step)),
            solvable_solved=_solvable(observed, step),
        )
        for step in attempted
    )


def _affected(step: DependencyStep, reading: str) -> bool | None:
    found = step.unprovided if reading == UNPROVIDED else step.undeclared_imports
    return None if found is None else bool(found)


def _heuristic(step: DependencyStep, reading: str) -> bool:
    return reading == UNDECLARED_IMPORT and not any(
        item.get("basis") == "confirmed" for item in step.undeclared_imports or ()
    )


def _share(outcomes: Iterable[TrialOutcome], reading: str) -> ReadingShare:
    measured = affected = unmeasured = failed = judged = heuristic = 0
    for outcome in outcomes:
        for step in outcome.dependency_steps:
            hit = _affected(step, reading)
            if hit is None:
                unmeasured += 1
                continue
            measured += 1
            if not hit:
                continue
            affected += 1
            heuristic += _heuristic(step, reading)
            if step.solvable_solved is not None:
                judged += 1
                failed += not step.solvable_solved
    return ReadingShare(
        measured=measured,
        affected=affected,
        unmeasured=unmeasured,
        share=affected / measured if measured else None,
        solvable_failed=failed,
        solvable_judged=judged,
        heuristic=heuristic,
    )


def arm_dependencies(valid: Sequence[TrialOutcome]) -> dict[str, ReadingShare] | None:
    """Both readings over an arm's valid trials; ``None`` when no trial has dependency steps."""
    if not any(outcome.dependency_steps for outcome in valid):
        return None
    return {reading: _share(valid, reading) for reading in READINGS}


def _dependency_warning(
    baseline: str, treatment: str, reading: str, left: float | None, right: float | None
) -> str | None:
    if left is None and right is None:
        return None
    if left is None or right is None:
        missing = baseline if left is None else treatment
        return (
            f"{reading}: not comparable; arm {missing} has no measured step "
            f"({baseline} vs {treatment})"
        )
    if abs(left - right) >= MATERIAL_SHARE_DIFFERENCE:
        return (
            f"{reading}: dependency availability differs between arms in the share of "
            f"affected steps ({baseline} {left:.0%} vs {treatment} {right:.0%}); "
            "inspect the affected steps"
        )
    return None


def dependency_warnings(
    baseline: str, arms: Mapping[str, dict[str, ReadingShare] | None], treatments: Sequence[str]
) -> tuple[str, ...]:
    """A warning per treatment arm and reading whose share differs materially from the baseline's.

    An arm with a measured share against one without is reported as not comparable.
    """
    warnings: list[str] = []
    base = arms.get(baseline)
    for treatment in treatments:
        other = arms.get(treatment)
        for reading in READINGS:
            left = base[reading].share if base else None
            right = other[reading].share if other else None
            warning = _dependency_warning(baseline, treatment, reading, left, right)
            if warning is not None:
                warnings.append(warning)
    return tuple(warnings)
