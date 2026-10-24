"""Oracle validation checks for an imported problem (plan task 0.5 as a command).

For each step k (checkpoint k):

- ``reference_k_passes_1..k``: reference solution k passes every graded case;
- ``reference_k-1_fails_k``: reference k-1 (the empty starter for the first
  step and for a one-shot problem) fails at least one case;
- ``repeat_identical_{positive,negative}``: every repeat gives identical
  per-case outcomes and group counts;
- ``native_parity_{positive,negative}``: SCB's native runner grades the same
  snapshots case by case like the Raidar verifier (``not_run`` when skipped).

A step "passes" under the strict all-cases rule. An infrastructure failure (no
verifier record, a grader infra code, no native evaluation) is never a pass or a
fail: the check it touches is ``infra_error`` and, unless another check found
a real defect, the verdict is ``infra_failure`` - evidence is missing, so the
problem is neither validated nor excluded.

Verdict precedence: any ``fail`` -> ``excluded``; else any ``infra_error`` ->
``infra_failure``; else any ``not_run`` -> ``validated_partial`` (the report
names the skipped checks); else ``validated``.

The report also carries the spec-determinacy audit (``spec_determinacy``,
``raidar.problems.audit``) when it ran. The audit never changes the verdict: a
problem whose tests assert undetermined values stays usable, and its pinned
errata (``raidar.problems.errata``) is the policy response.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from raidar.spec.metrics import (
    DEFAULT_METRIC_REGISTRY,
    checkpoint_pass_rate,
    problem_correctness,
)
from raidar.verifier.models import CaseGroup, CaseStatus, VerifierGrade

POSITIVE = "positive"
NEGATIVE = "negative"
VARIANTS = (POSITIVE, NEGATIVE)
_DIFF_LIMIT = 8


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INFRA = "infra_error"
    NOT_RUN = "not_run"


class Verdict(StrEnum):
    VALIDATED = "validated"
    VALIDATED_PARTIAL = "validated_partial"
    EXCLUDED = "excluded"
    INFRA_FAILURE = "infra_failure"


# Verdicts under which a problem may be locked and run (F13): every check that
# ran passed. ``validated_partial`` stays usable; its skipped checks are recorded.
USABLE_VERDICTS = frozenset({Verdict.VALIDATED, Verdict.VALIDATED_PARTIAL})
# Checks a usable verdict may never skip: plan task 0.5 requires identical
# repeat runs, so only the native-parity arm may be left out of a partial pass.
REQUIRED_CHECK_PREFIX = "repeat_identical"


@dataclass(frozen=True)
class Grade:
    """One grading of one snapshot at one step, reduced to comparable outcomes."""

    source: str
    step: str
    groups: Mapping[str, tuple[int, int]]
    outcomes: Mapping[str, str]
    infra_failure: str | None = None

    @classmethod
    def from_verifier(cls, grade: VerifierGrade, source: str = "oracle") -> Grade:
        outcomes = {
            f"{case.checkpoint_origin}::{case.id.split('::', 1)[-1]}": case.status.value
            for case in grade.cases
        }
        groups = {
            group.value: (count.passed, count.total)
            for group, count in grade.group_counts().items()
        }
        infra = grade.infra_failure.code if grade.infra_failure else None
        return cls(source, grade.step, groups, outcomes, infra)

    @property
    def passed(self) -> int:
        return sum(passed for passed, _ in self.groups.values())

    @property
    def total(self) -> int:
        return sum(total for _, total in self.groups.values())

    @property
    def has_verdict(self) -> bool:
        return self.infra_failure is None and bool(self.outcomes)

    @property
    def all_pass(self) -> bool:
        return self.infra_failure is None and self.total > 0 and self.passed == self.total

    def verdicts(self) -> dict[str, bool]:
        """Pass/non-pass per graded case; skipped cases leave the denominator."""
        return {
            key: status == CaseStatus.PASSED.value
            for key, status in self.outcomes.items()
            if status != CaseStatus.SKIPPED.value
        }

    def compact(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "total": self.total,
            "all_pass": self.all_pass,
            "groups": {group: list(counts) for group, counts in sorted(self.groups.items())},
            "infra_failure": self.infra_failure,
        }


StepGrades = Mapping[str, Grade]


@dataclass(frozen=True)
class Check:
    name: str
    status: CheckStatus
    detail: tuple[str, ...] = ()


def _limited(notes: list[str]) -> list[str]:
    extra = len(notes) - _DIFF_LIMIT
    return notes[:_DIFF_LIMIT] + ([f"... {extra} more"] if extra > 0 else [])


def _word(passed: bool) -> str:
    return "pass" if passed else "fail"


def diff_grades(a: Grade, b: Grade) -> list[str]:
    va, vb = a.verdicts(), b.verdicts()
    notes = [f"only in {a.source}: {key}" for key in sorted(va.keys() - vb.keys())]
    notes += [f"only in {b.source}: {key}" for key in sorted(vb.keys() - va.keys())]
    notes += [
        f"{key}: {a.source}={_word(va[key])} {b.source}={_word(vb[key])}"
        for key in sorted(va.keys() & vb.keys())
        if va[key] != vb[key]
    ]
    return _limited(notes)


def _no_verdict(label: str, grade: Grade | None) -> str:
    reason = "not graded" if grade is None else grade.infra_failure or "no graded cases"
    return f"{label}: no verdict ({reason})"


def _check(name: str, failures: list[str], infra: list[str]) -> Check:
    """A real failure outranks missing evidence; both are listed in the detail."""
    if failures:
        status = CheckStatus.FAIL
    elif infra:
        status = CheckStatus.INFRA
    else:
        status = CheckStatus.PASS
    return Check(name, status, tuple(failures + infra))


def check_expectation(name: str, runs: Sequence[StepGrades], *, expect_pass: bool) -> Check:
    failures: list[str] = []
    infra: list[str] = []
    for index, grades in enumerate(runs):
        for step, grade in grades.items():
            if not grade.has_verdict:
                infra.append(_no_verdict(f"repeat {index} {step}", grade))
            elif grade.all_pass != expect_pass:
                expected = "all pass" if expect_pass else "at least one failure"
                failures.append(
                    f"repeat {index} {step}: {grade.passed}/{grade.total} (expected {expected})"
                )
    return _check(name, failures, infra)


def check_determinism(name: str, runs: Sequence[StepGrades]) -> Check:
    if len(runs) < 2:
        return Check(name, CheckStatus.NOT_RUN, ("needs at least 2 repeats",))
    failures: list[str] = []
    infra: list[str] = []
    first = runs[0]
    for index, grades in enumerate(runs[1:], start=1):
        for step, grade in grades.items():
            reference = first.get(step)
            if reference is None or not reference.has_verdict:
                infra.append(_no_verdict(f"repeat 0 {step}", reference))
            elif not grade.has_verdict:
                infra.append(_no_verdict(f"repeat {index} {step}", grade))
            elif grade.verdicts() != reference.verdicts() or grade.groups != reference.groups:
                diff = "; ".join(diff_grades(reference, grade)) or "group counts differ"
                failures.append(f"repeat {index} vs 0 {step}: {diff}")
    return _check(name, failures, sorted(set(infra)))


def check_parity(name: str, oracle: StepGrades, native: StepGrades | None) -> Check:
    if native is None:
        return Check(name, CheckStatus.NOT_RUN, ("native runner not run",))
    failures: list[str] = []
    infra: list[str] = []
    for step, grade in oracle.items():
        other = native.get(step)
        if not grade.has_verdict:
            infra.append(_no_verdict(f"{step} oracle", grade))
            continue
        if other is None or not other.has_verdict:
            infra.append(_no_verdict(f"{step} native", other))
            continue
        if grade.groups != other.groups:
            failures.append(
                f"{step}: groups oracle={dict(grade.groups)} native={dict(other.groups)}"
            )
        per_case = diff_grades(grade, other)
        if per_case:
            failures.append(f"{step}: " + "; ".join(per_case))
    return _check(name, failures, infra)


@dataclass(frozen=True)
class ValidationRuns:
    """Oracle grades per variant and repeat, plus optional native grades per variant."""

    oracle: Mapping[str, Sequence[StepGrades]]
    native: Mapping[str, StepGrades] | None = None


def run_checks(runs: ValidationRuns) -> list[Check]:
    checks = [
        check_expectation("reference_k_passes_1..k", runs.oracle[POSITIVE], expect_pass=True),
        check_expectation("reference_k-1_fails_k", runs.oracle[NEGATIVE], expect_pass=False),
        check_determinism("repeat_identical_positive", runs.oracle[POSITIVE]),
        check_determinism("repeat_identical_negative", runs.oracle[NEGATIVE]),
    ]
    for variant in VARIANTS:
        native = runs.native.get(variant) if runs.native is not None else None
        checks.append(check_parity(f"native_parity_{variant}", runs.oracle[variant][0], native))
    return checks


_VERDICT_BY_STATUS = (
    (CheckStatus.FAIL, Verdict.EXCLUDED),
    (CheckStatus.INFRA, Verdict.INFRA_FAILURE),
    (CheckStatus.NOT_RUN, Verdict.VALIDATED_PARTIAL),
)


def verdict(checks: Sequence[Check]) -> Verdict:
    statuses = {check.status for check in checks}
    for status, outcome in _VERDICT_BY_STATUS:
        if status in statuses:
            return outcome
    return Verdict.VALIDATED


def _reference_correctness(positive: StepGrades) -> tuple[Decimal | None, list[str]]:
    """The reference solution's C (the fork's mean pass rate), or why it is ineligible.

    Unavailable is never zero (F6): an ungraded step makes C ineligible with the
    infra reason, and a step with no graded case with the fork's
    ``canonical_test_denominator_zero``.
    """
    ungraded = sorted(step for step, grade in positive.items() if not grade.has_verdict)
    if ungraded:
        return None, [
            f"verifier_infra_failure: {step} ({positive[step].infra_failure})" for step in ungraded
        ]
    empty = sorted(step for step, grade in positive.items() if grade.total == 0)
    if empty:
        return None, [f"no_graded_cases: {step}" for step in empty]
    rates = [checkpoint_pass_rate(grade.passed, grade.total) for grade in positive.values()]
    return problem_correctness(rates), []


def policy_preview(positive: StepGrades, checkpoints: int) -> dict[str, object]:
    """Headline metric selected by K, applied to the reference solution's C.

    Only correctness is known here; the other components come from scorers
    (WS-G), so the preview shows which components the policy uses, which are
    ``not_applicable`` and which are still missing - it is not a score.
    """
    policy = DEFAULT_METRIC_REGISTRY.default_for_checkpoints(checkpoints)
    value, reasons = _reference_correctness(positive)
    if value is None:
        result = policy.score(checkpoints=checkpoints, components={}, ineligible={"C": reasons})
    else:
        result = policy.score(checkpoints=checkpoints, components={"C": value})
    correctness = None if value is None else str(value)
    return {
        "policy": policy.ref,
        "selection": "default_by_k",
        "checkpoints": checkpoints,
        "reference_correctness": correctness,
        "components": {key: status.value for key, status in result.components.items()},
        "eligible": result.eligible,
        "reasons": [asdict(reason) for reason in result.reasons],
    }


def _compact(grades: StepGrades) -> dict[str, object]:
    return {step: grade.compact() for step, grade in grades.items()}


@dataclass
class ValidationReport:
    problem: str
    manifest_sha256: str
    mode: str
    steps: tuple[str, ...]
    engine: str
    repeats: int
    checks: list[Check]
    runs: ValidationRuns
    environment: dict[str, object] = field(default_factory=dict)
    spec_determinacy: dict[str, object] | None = None

    @property
    def verdict(self) -> Verdict:
        return verdict(self.checks)

    def to_json(self) -> dict[str, object]:
        oracle = {
            variant: [_compact(run) for run in runs] for variant, runs in self.runs.oracle.items()
        }
        native = (
            {variant: _compact(grades) for variant, grades in self.runs.native.items()}
            if self.runs.native is not None
            else None
        )
        return {
            "kind": "raidar-problem-validation",
            "schema_version": 1,
            "problem": self.problem,
            "manifest_sha256": self.manifest_sha256,
            "mode": self.mode,
            "steps": list(self.steps),
            "engine": self.engine,
            "repeats": self.repeats,
            "verdict": self.verdict.value,
            "not_run": [c.name for c in self.checks if c.status is CheckStatus.NOT_RUN],
            "checks": [
                {"name": c.name, "status": c.status.value, "detail": list(c.detail)}
                for c in self.checks
            ],
            "grades": {"oracle": oracle, "native": native},
            "headline_policy": policy_preview(self.runs.oracle[POSITIVE][0], len(self.steps)),
            "environment": self.environment,
            "group_order": [group.value for group in CaseGroup],
            **(
                {"spec_determinacy": self.spec_determinacy}
                if self.spec_determinacy is not None
                else {}
            ),
        }


class ValidationRecordError(ValueError):
    """A recorded validation report is missing, unreadable or not for this manifest."""


@dataclass(frozen=True)
class ValidationRecord:
    """The verdict a committed ``<version>.validation.json`` records for one manifest."""

    verdict: Verdict
    manifest_sha256: str
    not_run: tuple[str, ...]

    @property
    def missing_required(self) -> tuple[str, ...]:
        return tuple(name for name in self.not_run if name.startswith(REQUIRED_CHECK_PREFIX))

    @property
    def usable(self) -> bool:
        return self.verdict in USABLE_VERDICTS and not self.missing_required

    def lock_metadata(self) -> dict[str, object]:
        """What the lock pins: the verdict and what it did not check.

        The report's bytes are deliberately not pinned. They carry
        machine- and run-specific fields (Docker context, Harbor version,
        native-runner commit, repeat count, per-run grades), so re-validating
        an unchanged problem elsewhere would change ``lock_sha256`` and orphan
        every earlier run. The manifest the report is for is the reference's
        own digest.
        """
        return {"verdict": self.verdict.value, "not_run": list(self.not_run)}


def read_validation_record(path: Path) -> ValidationRecord:
    try:
        data = json.loads(path.read_bytes())
        return ValidationRecord(
            verdict=Verdict(data["verdict"]),
            manifest_sha256=str(data["manifest_sha256"]),
            not_run=tuple(str(name) for name in data.get("not_run", ())),
        )
    except FileNotFoundError as exc:
        raise ValidationRecordError(f"no validation report at {path}") from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValidationRecordError(f"unreadable validation report {path}: {exc}") from exc
