"""Spec-determinacy audit: flag verifier cases that assert what the spec does not determine.

Two independent kinds of evidence, combined per case:

1. **Static** (``raidar.problems.determinacy``): every audited assertion of a
   test is classified against the specification of its origin checkpoint and
   all earlier ones. ``undetermined`` means the asserted wording, content or
   byte format is not stated there.
2. **Dynamic** (``raidar.problems.variants``): reference k and its
   spec-conformant variants are graded at step k by the Raidar verifier. A case
   is *sensitive* to a variant when it passes on the reference and does not pass
   on the variant.

A case is **flagged** when its test has an undetermined assertion of the kind
a variant it is sensitive to rewrites (error text for ``neutral_error_text``,
output bytes for ``reformatted_json``). Undetermined assertions whose cases no
variant breaks are listed as ``unconfirmed``; sensitive cases whose matching
assertions are all determined are ``sensitive_determined`` (the spec does pin
them); sensitive cases with no audited assertion of the matching kind are
``sensitive_unexplained``. Only flagged cases are errata candidates
(``raidar.problems.errata``); nothing here changes a test or a grade.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from raidar.problems.determinacy import (
    AUDIT_VERSION,
    AssertionKind,
    AuditedAssertion,
    Determination,
    SpecText,
    classify,
    scan_tests,
)
from raidar.problems.errata import report_entries
from raidar.problems.store import ImportedProblem
from raidar.problems.variant_shim import NEUTRAL_ERROR_TEXT, REFORMATTED_JSON
from raidar.problems.variants import VARIANTS, build_variant
from raidar.verifier.grader import DEFAULT_RUN_TIMEOUT_S, grade_step
from raidar.verifier.models import CaseStatus, VerifierGrade
from raidar.verifier.sandbox import GradingSandbox

REFERENCE = "reference"
METHOD = (
    "Static: each audited assertion of a test (error-detail/message equality, containment "
    "or pattern; byte-level stdout comparison) is classified against the text of its origin "
    "checkpoint's specification and every earlier one (rules below). Dynamic: reference k "
    "and spec-conformant variants of it (variants below) are graded at step k; a case is "
    "sensitive to a variant when it passes on the reference and not on the variant. A case "
    "is flagged when its test has an undetermined assertion of the kind a variant it is "
    "sensitive to rewrites."
)
RULES = {
    "literal-in-spec": "determined: the asserted literal occurs in the specification text",
    "format-stated": "determined: the specification names an output byte format",
    "format-unstated": "undetermined: stdout compared byte for byte; no byte format stated",
    "wording-unstated": (
        "undetermined: an error-text literal in neither the specification nor the test's inputs"
    ),
    "exact-stated": "determined: a paragraph states the exact content of the category's detail",
    "exact-unstated": (
        "undetermined: error text must equal an input value; no paragraph states exact content"
    ),
    "content-stated": "determined: a paragraph states what the category's detail contains",
    "content-unstated": (
        "undetermined: error text must contain an input value or match a pattern; no paragraph "
        "states what the category's detail contains"
    ),
}
_KIND_VARIANT = {
    AssertionKind.ERROR_TEXT_EQUALS: NEUTRAL_ERROR_TEXT,
    AssertionKind.ERROR_TEXT_CONTAINS: NEUTRAL_ERROR_TEXT,
    AssertionKind.ERROR_TEXT_PATTERN: NEUTRAL_ERROR_TEXT,
    AssertionKind.OUTPUT_BYTES: REFORMATTED_JSON,
}

StepCases = Mapping[str, Mapping[str, str]]
"""``{step: {case id: status}}`` for the cases whose origin is that step."""


@dataclass(frozen=True)
class StepGradeSummary:
    passed: int
    total: int
    infra_failure: str | None
    origin_cases: dict[str, str]

    @classmethod
    def from_grade(cls, grade: VerifierGrade) -> StepGradeSummary:
        counts = grade.group_counts().values()
        return cls(
            passed=sum(count.passed for count in counts),
            total=sum(count.total for count in counts),
            infra_failure=grade.infra_failure.code if grade.infra_failure else None,
            origin_cases={
                case.id: case.status.value
                for case in grade.cases
                if case.checkpoint_origin == grade.checkpoint
            },
        )


@dataclass(frozen=True)
class DynamicEvidence:
    """Reference and variant grades per step, or why the dynamic half did not run."""

    grades: Mapping[str, Mapping[str, StepGradeSummary]]
    """``{reference | variant: {step: summary}}``."""
    not_run: str | None = None
    environment: Mapping[str, object] | None = None


def function_of(case_id: str) -> str:
    """``test_x.py::test_y[param]`` -> ``test_x.py::test_y``."""
    return case_id.split("[", 1)[0]


def spec_texts(problem: ImportedProblem) -> dict[str, SpecText]:
    """Each step's cumulative specification (its own and every earlier instruction)."""
    texts: dict[str, SpecText] = {}
    instructions: list[str] = []
    for step in problem.manifest.steps:
        path = problem.task_dir / "steps" / step.name / "instruction.md"
        if not path.is_file():
            path = problem.task_dir / "instruction.md"
        instructions.append(path.read_text())
        texts[step.name] = SpecText.from_markdown(list(instructions))
    return texts


def origins(problem: ImportedProblem) -> dict[str, str]:
    """Test file -> the first step that grades it (its cases' origin checkpoint)."""
    found: dict[str, str] = {}
    for step in problem.manifest.steps:
        for name in step.test_files:
            found.setdefault(name, step.name)
    return found


def static_findings(
    problem: ImportedProblem,
) -> list[tuple[AuditedAssertion, str, Determination]]:
    """Every audited assertion with its origin step and determination."""
    origin = origins(problem)
    texts = spec_texts(problem)
    found = scan_tests(problem.bundle.tests_dir, list(origin))
    return [
        (item, origin[item.test_file], classify(item, texts[origin[item.test_file]]))
        for item in found
    ]


def _assertion_json(item: AuditedAssertion, step: str, verdict: Determination) -> dict[str, object]:
    return {
        "function": item.function_id,
        "checkpoint_origin": step,
        "line": item.line,
        "kind": item.kind.value,
        "assertion": item.source,
        "expected": item.expected,
        "category": item.category,
        "rule": verdict.rule,
        "why": verdict.why,
    }


def _sensitive(dynamic: DynamicEvidence, variant: str, step: str) -> set[str]:
    reference = dynamic.grades.get(REFERENCE, {}).get(step)
    graded = dynamic.grades.get(variant, {}).get(step)
    if reference is None or graded is None or reference.infra_failure or graded.infra_failure:
        return set()
    passed = CaseStatus.PASSED.value
    return {
        case
        for case, status in reference.origin_cases.items()
        if status == passed
        and graded.origin_cases.get(case) not in (passed, CaseStatus.SKIPPED.value)
    }


def _grade_json(summary: StepGradeSummary, sensitive: set[str] | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "passed": summary.passed,
        "total": summary.total,
        "infra_failure": summary.infra_failure,
    }
    if sensitive is not None:
        payload["sensitive"] = sorted(sensitive)
    return payload


@dataclass
class _Combiner:
    findings: list[tuple[AuditedAssertion, str, Determination]]
    dynamic: DynamicEvidence

    def __post_init__(self) -> None:
        self.undetermined: dict[str, list[tuple[AuditedAssertion, str, Determination]]] = {}
        self.audited: dict[str, list[AuditedAssertion]] = {}
        for item, step, verdict in self.findings:
            self.audited.setdefault(item.function_id, []).append(item)
            if not verdict.determined:
                self.undetermined.setdefault(item.function_id, []).append((item, step, verdict))

    def steps(self) -> dict[str, object]:
        reference = self.dynamic.grades.get(REFERENCE, {})
        steps: dict[str, object] = {}
        for step, summary in reference.items():
            variants = {
                variant: _grade_json(grades[step], _sensitive(self.dynamic, variant, step))
                for variant, grades in self.dynamic.grades.items()
                if variant != REFERENCE and step in grades
            }
            steps[step] = {REFERENCE: _grade_json(summary, None), "variants": variants}
        return steps

    def case_sensitivity(self) -> dict[str, list[str]]:
        """Case id -> the variants it is sensitive to (cases of graded steps only)."""
        cases: dict[str, list[str]] = {}
        for step, summary in self.dynamic.grades.get(REFERENCE, {}).items():
            for case in summary.origin_cases:
                cases[case] = sorted(
                    variant
                    for variant in self.dynamic.grades
                    if variant != REFERENCE and case in _sensitive(self.dynamic, variant, step)
                )
        return cases

    def classify_case(self, case: str, variants: list[str]) -> tuple[str, dict[str, object]] | None:
        function = function_of(case)
        undetermined = [
            entry
            for entry in self.undetermined.get(function, [])
            if _KIND_VARIANT[entry[0].kind] in variants
        ]
        if undetermined:
            return "flagged", {
                "case": case,
                "checkpoint_origin": undetermined[0][1],
                "variants": variants,
                "assertions": [_assertion_json(*entry) for entry in undetermined],
            }
        if self.undetermined.get(function) and not variants:
            return "unconfirmed", {"case": case, "reason": "no variant changed its outcome"}
        if not variants:
            return None
        kinds = {_KIND_VARIANT[item.kind] for item in self.audited.get(function, [])}
        return ("sensitive_determined" if kinds & set(variants) else "sensitive_unexplained"), {
            "case": case,
            "variants": variants,
        }


def _summary(
    flagged: Sequence[Mapping[str, object]], buckets: Mapping[str, list[object]]
) -> dict[str, object]:
    by_step: dict[str, int] = {}
    by_rule: dict[str, int] = {}
    for entry in flagged:
        step = str(entry["checkpoint_origin"])
        by_step[step] = by_step.get(step, 0) + 1
        rules = {str(a["rule"]) for a in report_entries(entry.get("assertions"))}
        for rule in rules:
            by_rule[rule] = by_rule.get(rule, 0) + 1
    return {
        "flagged": len(flagged),
        "flagged_by_checkpoint": dict(sorted(by_step.items())),
        "flagged_by_rule": dict(sorted(by_rule.items())),
        **{name: len(items) for name, items in buckets.items()},
    }


def audit_report(problem: ImportedProblem, dynamic: DynamicEvidence) -> dict[str, object]:
    """The ``spec_determinacy`` section of the validation report."""
    findings = static_findings(problem)
    combiner = _Combiner(findings, dynamic)
    buckets: dict[str, list[object]] = {
        "flagged": [],
        "unconfirmed": [],
        "sensitive_determined": [],
        "sensitive_unexplained": [],
    }
    if dynamic.not_run is None:
        for case, variants in sorted(combiner.case_sensitivity().items()):
            outcome = combiner.classify_case(case, variants)
            if outcome is not None:
                buckets[outcome[0]].append(outcome[1])
    rules: dict[str, int] = {}
    for _, _, verdict in findings:
        rules[verdict.rule] = rules.get(verdict.rule, 0) + 1
    flagged = buckets.pop("flagged")
    return {
        "audit_version": AUDIT_VERSION,
        "method": METHOD,
        "rules": RULES,
        "variants": VARIANTS,
        "dynamic": {
            "status": "not_run" if dynamic.not_run else "ran",
            "reason": dynamic.not_run,
            "environment": dict(dynamic.environment or {}),
        },
        "assertions": {"audited": len(findings), "by_rule": dict(sorted(rules.items()))},
        "static_undetermined": [
            _assertion_json(item, step, verdict)
            for item, step, verdict in findings
            if not verdict.determined
        ],
        "steps": combiner.steps(),
        "flagged": flagged,
        **buckets,
        "summary": _summary(report_entries(flagged), buckets),
    }


async def grade_audit_variants(
    problem: ImportedProblem,
    sandbox: GradingSandbox,
    *,
    concurrency: int,
    timeout_s: float = DEFAULT_RUN_TIMEOUT_S,
    work_root: Path | None = None,
) -> DynamicEvidence:
    """Grade reference k and each variant of it at step k (fresh sandbox per grade)."""
    entry = problem.manifest.entry_file
    if entry is None:
        return DynamicEvidence({}, not_run="the problem records no entry_file to wrap")
    gate = asyncio.Semaphore(concurrency)
    steps = [step.name for step in problem.manifest.steps]
    with tempfile.TemporaryDirectory(prefix="raidar-problem-audit-", dir=work_root) as tmp:
        work = Path(tmp)

        async def one(variant: str, step: str) -> StepGradeSummary:
            reference = problem.reference(step)
            snapshot = (
                reference
                if variant == REFERENCE
                else build_variant(reference, entry, variant, work / "snapshots" / variant / step)
            )
            async with gate:
                result = await grade_step(
                    problem.bundle,
                    step,
                    snapshot,
                    sandbox,
                    work / "runs" / variant / step,
                    coverage=False,
                    timeout_s=timeout_s,
                )
            return StepGradeSummary.from_grade(result.grade)

        keys = [(variant, step) for variant in (REFERENCE, *VARIANTS) for step in steps]
        results = await asyncio.gather(*(one(*key) for key in keys))
    grades: dict[str, dict[str, StepGradeSummary]] = {}
    for (variant, step), summary in zip(keys, results, strict=True):
        grades.setdefault(variant, {})[step] = summary
    return DynamicEvidence(grades)
