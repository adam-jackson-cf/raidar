"""Oracle validation checks for an imported problem (plan task 0.5 as a command).

For each step k (checkpoint k):

- ``solvable_set``: the cases reference solution k passes in *every* repeat,
  regression cases from earlier checkpoints included, form the step's solvable
  set (the benchmark-neutral reading of factory-x-loops
  ``evaluation/analysis/solving.py`` at ac5db0f). Runs of byte-identical reference code
  (ignoring ``__pycache__``) are pooled as repeats of each other, across steps and across
  the positive and negative variants; a case whose outcome varies anywhere in a pool is
  nondeterministic and is left out of the solvable set of *every* step. A step whose set
  holds no *new* case (an id no earlier step collected) is excluded on its own as
  ``no_new_scoreable_tests``; the other steps stay usable. The check fails only when *no*
  step is usable, and the per-step sets are what the lock pins;
- ``reference_k-1_fails_k``: a reading, never a gate: whether reference k-1 (the empty
  starter for the first step and for a one-shot problem) fails a case at step k, or
  ``not_informative`` naming the pair when the two references are identical;
- ``repeat_identical_{positive,negative}``: positive repeats are compared case
  by case (differences are listed as nondeterministic cases, not failures);
  negative repeats must give identical per-case outcomes and group counts, except for
  cases quarantined by pooling;
- ``native_parity_{positive,negative}``: SCB's native runner grades the same
  snapshots case by case like the Raidar verifier (``not_run`` when skipped).

An infrastructure failure (no verifier record, a grader infra code, no native
evaluation) is never a pass or a fail: the check it touches is ``infra_error``
and, unless another check found a real defect, the verdict is
``infra_failure`` - evidence is missing, so the problem is neither validated
nor excluded.

Verdict precedence: any ``fail`` -> ``excluded``; else any ``infra_error`` ->
``infra_failure``; else any ``not_run`` -> ``validated_partial`` (the report
names the skipped checks); else ``validated``.

The report also carries the spec-determinacy audit (``spec_determinacy``,
``raidar.design.problem.audit``) when it ran. The audit never changes the verdict: a
problem whose tests assert undetermined values stays usable, and its pinned
errata (``raidar.design.problem.errata``) is the policy response.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from raidar.contract.canonical import digest_json
from raidar.grading.dependency_pins import REFUSAL_REASON, DependencyRefusal
from raidar.grading.models import CaseGroup, CaseStatus, VerifierGrade
from raidar.grading.solvable import case_key
from raidar.scoring.framework.metrics import (
    DEFAULT_METRIC_REGISTRY,
    checkpoint_pass_rate,
    problem_correctness,
)

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
        outcomes = {case_key(case): case.status.value for case in grade.cases}
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


NOT_INFORMATIVE = "not_informative"
NO_NEW_SCOREABLE_TESTS = "no_new_scoreable_tests"
REFERENCE_READING = "reference_k-1_fails_k"


def reference_tree_digest(root: Path) -> str:
    """Digest of every regular file's relative path and bytes, ignoring ``__pycache__``.

    Two references with the same digest are the same code: grading them is a repeat of the
    same run (``solvable_sets`` pools their outcomes).
    """
    digest = hashlib.sha256(b"raidar-reference\0")
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix().encode()):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or not path.is_file():
            continue
        name = relative.as_posix().encode()
        content = hashlib.sha256(path.read_bytes()).digest()
        digest.update(len(name).to_bytes(8, "big") + name + content)
    return digest.hexdigest()


def _identity(runs: ValidationRuns, variant: str, step: str) -> str:
    """The grader-input identity of one (variant, step): its reference digest, else its own."""
    return runs.references.get(variant, {}).get(step) or f"{variant}/{step}"


def _positive_steps(runs: ValidationRuns) -> list[str]:
    return list(dict.fromkeys(step for grades in runs.oracle[POSITIVE] for step in grades))


def check_reference_reading(name: str, runs: ValidationRuns) -> Check:
    """The old k-1-fails-k check as a reading: never fails, never excludes.

    Per step: ``pass`` (the previous reference fails a case: the expected shape), ``fail`` (it
    passes every case in a repeat), or ``not_informative`` when both references are the same
    code (the pair is named), because an identical pair gives no negative evidence.
    """
    steps = _positive_steps(runs)
    notes: list[str] = []
    infra: list[str] = []
    for index, step in enumerate(steps):
        previous = steps[index - 1] if index else "empty starter"
        if _identity(runs, NEGATIVE, step) == _identity(runs, POSITIVE, step):
            pair = f"{previous} and {step} references are identical"
            notes.append(f"{step}: {NOT_INFORMATIVE} ({pair})")
            continue
        word = "pass"
        missing = False
        for grades in runs.oracle[NEGATIVE]:
            grade = grades.get(step)
            if grade is None or not grade.has_verdict:
                infra.append(_no_verdict(f"{step} negative", grade))
                missing = True
            elif grade.all_pass:
                word = "fail"
        if not missing:
            notes.append(f"{step}: {word} ({previous} reference on {step} tests)")
    return Check(name, CheckStatus.INFRA if infra else CheckStatus.PASS, tuple(notes + infra))


@dataclass(frozen=True)
class StepSolvable:
    """One step's solvable set: cases passed in every repeat, and the cases that varied.

    ``exclusion`` is set (with verdict ``excluded``) when the step alone is unusable.
    """

    step: str
    verdict: Verdict
    solvable: tuple[str, ...] = ()
    nondeterministic: tuple[str, ...] = ()
    exclusion: str | None = None

    @property
    def digest(self) -> str:
        return solvable_digest(self.solvable)

    def to_json(self) -> dict[str, object]:
        return {
            "verdict": self.verdict.value,
            "exclusion": self.exclusion,
            "solvable": list(self.solvable),
            "size": len(self.solvable),
            "digest": self.digest,
            "nondeterministic": list(self.nondeterministic),
            "nondeterministic_count": len(self.nondeterministic),
        }


def solvable_digest(cases: Sequence[str]) -> str:
    """Digest of the exact sorted case-id set (``origin::case`` ids)."""
    return hashlib.sha256(json.dumps(sorted(cases), separators=(",", ":")).encode()).hexdigest()


def _verdict_grades(repeats: Sequence[StepGrades], step: str) -> list[Grade]:
    return [
        grade for grades in repeats if (grade := grades.get(step)) is not None and grade.has_verdict
    ]


def varying_cases(runs: ValidationRuns) -> frozenset[str]:
    """Case ids whose outcome varies anywhere in a pool of runs of identical reference code.

    Runs of byte-identical references on the same case id are repeats of each other, across
    steps and across the positive and negative variants. A case present in only some repeats
    of one (variant, step) also varies. The result applies to every step, whatever its code.
    """
    pooled: dict[tuple[str, str], set[str]] = {}
    varying: set[str] = set()
    for variant in VARIANTS:
        repeats = runs.oracle[variant]
        for step in dict.fromkeys(step for grades in repeats for step in grades):
            identity = _identity(runs, variant, step)
            varying |= _pool_step(_verdict_grades(repeats, step), identity, pooled)
    varying.update(case for (_, case), seen in pooled.items() if len(seen) > 1)
    return frozenset(varying)


def _pool_step(
    grades: Sequence[Grade], identity: str, pooled: dict[tuple[str, str], set[str]]
) -> set[str]:
    """Pool one step's outcomes by (identity, case); return cases varying across its repeats."""
    varying: set[str] = set()
    for case in {key for grade in grades for key in grade.outcomes}:
        seen = {grade.outcomes.get(case) for grade in grades}
        if len(seen) > 1:
            varying.add(case)
        pooled.setdefault((identity, case), set()).update(s for s in seen if s)
    return varying


def solvable_sets(runs: ValidationRuns) -> dict[str, StepSolvable]:
    """Per-step solvable sets from the positive-reference repeats, in step order.

    A step needs at least one *new* case (an id no earlier step collected, whatever its
    outcome) in its solvable set; without one the step alone is excluded as
    ``no_new_scoreable_tests`` and its set is empty.
    """
    repeats = runs.oracle[POSITIVE]
    varying = varying_cases(runs)
    sets: dict[str, StepSolvable] = {}
    collected: set[str] = set()
    for step in _positive_steps(runs):
        every = [grades.get(step) for grades in repeats]
        step_cases = {key for grade in every if grade is not None for key in grade.outcomes}
        sets[step] = _step_solvable(step, every, step_cases, frozenset(collected), varying)
        collected |= step_cases
    return sets


def _step_solvable(
    step: str,
    every: Sequence[Grade | None],
    step_cases: set[str],
    earlier: frozenset[str],
    varying: frozenset[str],
) -> StepSolvable:
    """One step's decision from its per-repeat grades (``None`` = step absent in a repeat)."""
    grades = [grade for grade in every if grade is not None and grade.has_verdict]
    if not every or len(grades) != len(every):
        return StepSolvable(step, Verdict.INFRA_FAILURE)
    passed = {
        case
        for case in step_cases
        if all(grade.outcomes.get(case) == CaseStatus.PASSED.value for grade in grades)
    }
    solvable = tuple(sorted(passed - varying))
    held = tuple(sorted(step_cases & varying))
    if not earlier.issuperset(solvable):
        return StepSolvable(step, Verdict.VALIDATED, solvable, held)
    return StepSolvable(step, Verdict.EXCLUDED, (), held, exclusion=NO_NEW_SCOREABLE_TESTS)


def _missing_evidence(sets: Mapping[str, StepSolvable]) -> list[str]:
    return [
        f"{step}: no verdict in every repeat"
        for step, one in sets.items()
        if one.verdict is Verdict.INFRA_FAILURE
    ]


def check_solvable(name: str, runs: ValidationRuns) -> Check:
    """Fails only when no step is usable; an excluded step is named and the others stay usable."""
    sets = solvable_sets(runs)
    infra = _missing_evidence(sets)
    if infra:
        return _check(name, [], infra)
    excluded = [f"{step}: {one.exclusion}" for step, one in sets.items() if one.exclusion]
    if all(one.exclusion for one in sets.values()):
        return _check(name, excluded, [])
    return Check(name, CheckStatus.PASS, tuple(excluded))


def check_positive_repeats(name: str, runs: ValidationRuns) -> Check:
    """Repeat evidence for the positive reference; differing cases are listed, not failed."""
    if len(runs.oracle[POSITIVE]) < 2:
        return Check(name, CheckStatus.NOT_RUN, ("needs at least 2 repeats",))
    sets = solvable_sets(runs)
    infra = _missing_evidence(sets)
    notes = [
        f"{step}: {len(one.nondeterministic)} nondeterministic"
        for step, one in sets.items()
        if one.nondeterministic
    ]
    return Check(name, CheckStatus.INFRA if infra else CheckStatus.PASS, tuple(infra + notes))


def _without(grade: Grade, cases: frozenset[str]) -> dict[str, bool]:
    return {case: ok for case, ok in grade.verdicts().items() if case not in cases}


def _compare_repeat_step(
    index: int,
    step: str,
    reference: Grade | None,
    grade: Grade,
    quarantined: frozenset[str],
    failures: list[str],
    infra: list[str],
) -> None:
    """Compare one repeat's step grade with repeat 0, appending to ``failures``/``infra``."""
    if reference is None or not reference.has_verdict:
        infra.append(_no_verdict(f"repeat 0 {step}", reference))
    elif not grade.has_verdict:
        infra.append(_no_verdict(f"repeat {index} {step}", grade))
    elif _differs(reference, grade, quarantined):
        diff = "; ".join(diff_grades(reference, grade)) or "group counts differ"
        failures.append(f"repeat {index} vs 0 {step}: {diff}")


def _differs(reference: Grade, grade: Grade, quarantined: frozenset[str]) -> bool:
    if _without(grade, quarantined) != _without(reference, quarantined):
        return True
    touched = quarantined & (set(grade.outcomes) | set(reference.outcomes))
    return grade.groups != reference.groups and not touched


def check_determinism(name: str, runs: ValidationRuns) -> Check:
    """Negative repeats agree case by case and in group counts.

    A case that varies in a pool of identical code is quarantined (``solvable_sets``) and is
    not compared here; group counts are then compared only for steps it does not touch.
    """
    repeats = runs.oracle[NEGATIVE]
    if len(repeats) < 2:
        return Check(name, CheckStatus.NOT_RUN, ("needs at least 2 repeats",))
    quarantined = varying_cases(runs)
    failures: list[str] = []
    infra: list[str] = []
    for index, grades in enumerate(repeats[1:], start=1):
        for step, grade in grades.items():
            reference = repeats[0].get(step)
            _compare_repeat_step(index, step, reference, grade, quarantined, failures, infra)
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
    """Oracle grades per variant and repeat, plus optional native grades per variant.

    ``references`` maps variant -> step -> ``reference_tree_digest`` of the graded snapshot;
    runs with the same digest are pooled as repeats of each other. Without it every
    (variant, step) is its own pool.
    """

    oracle: Mapping[str, Sequence[StepGrades]]
    native: Mapping[str, StepGrades] | None = None
    references: Mapping[str, Mapping[str, str]] = field(default_factory=dict)


DEPENDENCY_CHECK = "dependency_resolvable"


def check_dependencies(refusal: DependencyRefusal) -> Check:
    """A problem whose declared reference sets did not resolve is excluded."""
    detail = [f"{REFUSAL_REASON}: {refusal.detail}"]
    detail += [f"{item.requirement}: {item.reason}" for item in refusal.unprovided]
    return Check(DEPENDENCY_CHECK, CheckStatus.FAIL, tuple(detail))


def run_checks(runs: ValidationRuns) -> list[Check]:
    checks = [
        check_solvable("solvable_set", runs),
        check_reference_reading(REFERENCE_READING, runs),
        check_positive_repeats("repeat_identical_positive", runs),
        check_determinism("repeat_identical_negative", runs),
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
    so the preview shows which components the policy uses, which are
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


def _grades_json(runs: ValidationRuns) -> dict[str, object]:
    oracle = {
        variant: [_compact(run) for run in repeats] for variant, repeats in runs.oracle.items()
    }
    native = (
        {variant: _compact(grades) for variant, grades in runs.native.items()}
        if runs.native is not None
        else None
    )
    return {"oracle": oracle, "native": native}


@dataclass
class ValidationReport:
    problem: str
    manifest_sha256: str
    mode: str
    steps: tuple[str, ...]
    engine: str
    repeats: int
    checks: list[Check]
    runs: ValidationRuns | None
    environment: dict[str, object] = field(default_factory=dict)
    spec_determinacy: dict[str, object] | None = None

    @property
    def verdict(self) -> Verdict:
        return verdict(self.checks)

    def to_json(self) -> dict[str, object]:
        graded: dict[str, object] = {}
        if self.runs is not None:
            positive = self.runs.oracle[POSITIVE]
            graded = {
                "grades": _grades_json(self.runs),
                "solvable": {step: one.to_json() for step, one in solvable_sets(self.runs).items()},
                "reference_digests": {
                    variant: dict(steps) for variant, steps in self.runs.references.items()
                },
                "headline_policy": policy_preview(positive[0], len(self.steps)),
            }
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
            **graded,
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
    solvable: Mapping[str, StepSolvableRecord] = field(default_factory=dict)

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

    def solvable_lock(self) -> dict[str, object]:
        """The solvable sets the lock pins: ``{"sha256", "steps": {step: [sorted case ids]}}``.

        A step with an empty set maps to ``[]`` (its metric is ``unavailable``,
        never zero); a step the record has no evidence for is absent.
        """
        steps = {step: list(one.cases) for step, one in sorted(self.solvable.items())}
        return {"sha256": digest_json(steps), "steps": steps}

    def solvable_cases(self, step: str) -> tuple[str, ...] | None:
        """The step's exact sorted solvable set; ``None`` when none is usable (absent or empty)."""
        one = self.solvable.get(step)
        return one.cases if one is not None and one.cases else None

    def solvable_size(self, step: str) -> int:
        return len(self.solvable[step].cases) if step in self.solvable else 0

    def nondeterministic_count(self, step: str) -> int:
        return self.solvable[step].nondeterministic_count if step in self.solvable else 0


@dataclass(frozen=True)
class StepSolvableRecord:
    """A step's recorded solvable set as read back from the committed report."""

    verdict: Verdict
    cases: tuple[str, ...]
    digest: str
    nondeterministic_count: int
    exclusion: str | None = None


def _read_solvable(data: object) -> dict[str, StepSolvableRecord]:
    records: dict[str, StepSolvableRecord] = {}
    for step, raw in (data if isinstance(data, Mapping) else {}).items():
        cases = tuple(sorted(str(case) for case in raw["solvable"]))
        digest = str(raw["digest"])
        if digest != solvable_digest(cases):
            raise ValueError(f"solvable digest of step {step} does not match its cases")
        records[str(step)] = StepSolvableRecord(
            verdict=Verdict(raw["verdict"]),
            cases=cases,
            digest=digest,
            nondeterministic_count=int(raw["nondeterministic_count"]),
            exclusion=None if raw.get("exclusion") is None else str(raw["exclusion"]),
        )
    return records


def read_validation_record(path: Path) -> ValidationRecord:
    try:
        data = json.loads(path.read_bytes())
        return ValidationRecord(
            verdict=Verdict(data["verdict"]),
            manifest_sha256=str(data["manifest_sha256"]),
            not_run=tuple(str(name) for name in data.get("not_run", ())),
            solvable=_read_solvable(data.get("solvable")),
        )
    except FileNotFoundError as exc:
        raise ValidationRecordError(f"no validation report at {path}") from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValidationRecordError(f"unreadable validation report {path}: {exc}") from exc
