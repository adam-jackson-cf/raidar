"""Invariants of the ported SCB scoring models, kernels and formulas.

Each rule is the fork's: an evidence payload that is internally inconsistent
(unordered, out of range, contradictory eligibility) cannot be constructed,
and the kernels and formulas refuse incomplete inputs instead of scoring them.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from raidar.scorers.code_quality import arithmetic
from raidar.scorers.code_quality.lineage import (
    calculate_problem_rework,
    calculate_transition_rework,
)
from raidar.scorers.code_quality.lineage_models import (
    CoverageContextEvidence,
    LineageCandidate,
    LineageComponentEvidence,
    LineageLedgerEntry,
    OwnedLineEvidence,
    PhysicalLineEvidence,
    ProblemReworkEvidence,
    RegressionAttribution,
    ReworkCheckpointInput,
    SymbolIdentity,
    TransitionReworkEvidence,
)
from raidar.scorers.code_quality.models import (
    CheckpointCorrectnessEvidence,
    Eligibility,
    GraphSccEvidence,
    ProblemScore,
    ProductionEvidence,
    ProductionGraphEvidence,
    ProductionQualityRawEvidence,
    canonical_decimal,
)
from tests.code_quality_support import fork_goldens, golden_case


def _symbol(name: str = "f", **fields: Any) -> SymbolIdentity:
    return SymbolIdentity.model_validate(
        {"path": "m.py", "qualified_name": name, "kind": "function", "start_line": 1, "end_line": 3}
        | fields
    )


def _candidate(**fields: Any) -> dict[str, Any]:
    return {
        "prior": _symbol(),
        "current": _symbol(),
        "body_hash_equal": False,
        "structure_hash_equal": False,
        "signature_hash_equal": False,
        "accepted": True,
        "ambiguous_set_id": None,
        "match": "exact",
    } | fields


def _quality(**changes: Any) -> dict[str, Any]:
    raw = golden_case("synthetic_shop")["checkpoints"][0]["production"]["production_quality"]
    return {**raw, **changes}


def _graph(**changes: Any) -> dict[str, Any]:
    raw = golden_case("synthetic_shop")["checkpoints"][0]["production"]["production_graph"]
    return {**raw, **changes}


def _transition(**changes: Any) -> dict[str, Any]:
    raw = golden_case("synthetic_shop")["lineage"]["transitions"][1]
    return {**raw, **changes}


def _attribution(**changes: Any) -> dict[str, Any]:
    scenario = fork_goldens()["kernels"]["regression"][0]
    return {**scenario["result"], **changes}


def _reversed(values: list[Any]) -> list[Any]:
    return list(reversed(values))


INVALID: list[tuple[str, Callable[[], object]]] = [
    ("end line precedes", lambda: _symbol(start_line=5, end_line=4)),
    ("lowercase SHA-256", lambda: _symbol(body_sha256="ABC")),
    ("ambiguous set", lambda: LineageCandidate.model_validate(_candidate(ambiguous_set_id="x"))),
    ("requires a match kind", lambda: LineageCandidate.model_validate(_candidate(match="none"))),
    (
        "equal exact identities",
        lambda: LineageCandidate.model_validate(_candidate(current=_symbol("g"))),
    ),
    ("body and structure", lambda: LineageCandidate.model_validate(_candidate(match="hash"))),
    (
        "both endpoint sets",
        lambda: LineageComponentEvidence(
            component_id="a" * 64, priors=(), currents=(), ambiguous=True
        ),
    ),
    ("unique and ordered", lambda: OwnedLineEvidence(symbol=_symbol(), lines=(2, 1))),
    ("within the symbol span", lambda: OwnedLineEvidence(symbol=_symbol(), lines=(9,))),
    (
        "disposition must match",
        lambda: LineageLedgerEntry.model_validate(
            {"candidate": _candidate(), "disposition": "rejected", "reason": "x"}
        ),
    ),
    (
        "cannot exceed changed",
        lambda: TransitionReworkEvidence.model_validate(_transition(repeated_lines=999)),
    ),
    (
        "ordered by lineage candidates",
        lambda: TransitionReworkEvidence.model_validate(
            _transition(lineage_ledger=_reversed(_transition()["lineage_ledger"]))
        ),
    ),
    (
        "adjacent checkpoints",
        lambda: ProblemReworkEvidence(
            checkpoint_ids=("a", "b", "c"), transitions=(), final_ledger=()
        ),
    ),
    (
        "coverage lines must be unique",
        lambda: CoverageContextEvidence(node_id="n", path="p", executed_lines=(3, 2)),
    ),
    (
        "coverage lines must be positive",
        lambda: CoverageContextEvidence(node_id="n", path="p", executed_lines=(0,)),
    ),
    (
        "requires a score",
        lambda: RegressionAttribution.model_validate(_attribution(regression=None)),
    ),
    (
        "cannot attribute",
        lambda: RegressionAttribution.model_validate(
            _attribution(
                regression=None,
                eligibility={"eligible": False, "reasons": ["coverage_parity_unavailable"]},
            )
        ),
    ),
    (
        "numerator cannot exceed source",
        lambda: ProductionQualityRawEvidence.model_validate(_quality(union_numerator=10**6)),
    ),
    (
        "erosion numerator",
        lambda: ProductionQualityRawEvidence.model_validate(_quality(erosion_numerator="10000")),
    ),
    (
        "files must be ordered",
        lambda: ProductionQualityRawEvidence.model_validate(
            _quality(files=_reversed(_quality()["files"]))
        ),
    ),
    (
        "line evidence must be ordered",
        lambda: ProductionQualityRawEvidence.model_validate(
            _quality(clone_lines=_reversed(_quality()["clone_lines"]))
        ),
    ),
    (
        "Ruff diagnostics must be ordered",
        lambda: ProductionQualityRawEvidence.model_validate(
            _quality(
                ruff={
                    **_quality()["ruff"],
                    "diagnostics": [*_quality()["ruff"]["diagnostics"]] * 2
                    + [{"code": "SIM102", "path": "a.py", "start": [1, 1], "end": [2, 1]}],
                }
            )
        ),
    ),
    (
        "clone groups must be ordered",
        lambda: ProductionQualityRawEvidence.model_validate(
            _quality(
                clones=[
                    {"vector": ["a"], "occurrences": [["a.py", 1, 2], ["a.py", 3, 4]]},
                    *_quality()["clones"],
                ]
            )
        ),
    ),
    (
        "eligible symbols must be ordered",
        lambda: ProductionQualityRawEvidence.model_validate(
            _quality(eligible_symbols=_reversed(_quality()["eligible_symbols"]))
        ),
    ),
    (
        "excluded symbols must be ordered",
        lambda: ProductionQualityRawEvidence.model_validate(
            _quality(
                excluded_symbols=[
                    {"reason": "symbol_path_not_production", "kind": "function", "path": "z.py"},
                    *_quality()["excluded_symbols"],
                ]
            )
        ),
    ),
    ("UTF-8 byte order", lambda: GraphSccEvidence(members=("b", "a"), mass=Decimal(0))),
    (
        "cyclic mass cannot exceed",
        lambda: ProductionGraphEvidence.model_validate(_graph(cyclic_mass="999")),
    ),
    (
        "equal production edge weights",
        lambda: ProductionGraphEvidence.model_validate(_graph(total_mass="9999")),
    ),
    (
        "equal cyclic SCC mass",
        lambda: ProductionGraphEvidence.model_validate(_graph(cyclic_mass="0")),
    ),
    (
        "deduplicated ASCII-sorted",
        lambda: Eligibility(eligible=False, reasons=("b_code", "a_code")),
    ),
    ("unknown eligibility codes", lambda: Eligibility(eligible=False, reasons=("made_up",))),
    ("ineligible requires reasons", lambda: Eligibility(eligible=False)),
    ("requires V/E/H payloads", lambda: ProductionEvidence(eligibility=Eligibility(eligible=True))),
    (
        "declared zero",
        lambda: CheckpointCorrectnessEvidence(
            checkpoint_id="c", produced=False, passed=1, total=1, rate=Decimal(1)
        ),
    ),
    (
        "positive denominator",
        lambda: CheckpointCorrectnessEvidence(
            checkpoint_id="c", passed=0, total=0, rate=Decimal(0)
        ),
    ),
    (
        "passed / total",
        lambda: CheckpointCorrectnessEvidence(
            checkpoint_id="c", passed=1, total=2, rate=Decimal(1)
        ),
    ),
    (
        "cover configured checkpoints",
        lambda: ProblemScore.model_validate(
            {**fork_goldens()["formulas"]["problems"][0]["result"], "configured_checkpoints": 3}
        ),
    ),
    (
        "identifiers must be unique",
        lambda: ProblemScore.model_validate(
            {
                **fork_goldens()["formulas"]["problems"][0]["result"],
                "checkpoint_correctness": [
                    fork_goldens()["formulas"]["problems"][0]["result"]["checkpoint_correctness"][0]
                ]
                * 2,
            }
        ),
    ),
]


@pytest.mark.parametrize(("message", "build"), INVALID, ids=[item[0] for item in INVALID])
def test_inconsistent_evidence_cannot_be_constructed(
    message: str, build: Callable[[], object]
) -> None:
    with pytest.raises(ValidationError, match=message):
        build()


def test_decimals_are_quantised_only_when_finite() -> None:
    assert canonical_decimal(Decimal("0.1234567890126")) == Decimal("0.123456789013")
    with pytest.raises(ValueError, match="finite"):
        canonical_decimal(Decimal("NaN"))


def test_the_kernels_refuse_incomplete_or_mislabelled_inputs() -> None:
    removed = PhysicalLineEvidence(path="m.py", line=1, side="added")
    with pytest.raises(ValueError, match="side must match"):
        calculate_transition_rework((), (), (removed,), (), {})
    one = ReworkCheckpointInput(checkpoint_id="a", symbols=())
    with pytest.raises(ValueError, match="at least two"):
        calculate_problem_rework((one,))
    with pytest.raises(ValueError, match="must be unique"):
        calculate_problem_rework((one, one))
    gap = ReworkCheckpointInput(checkpoint_id="b", symbols=None)
    with pytest.raises(ValueError, match="complete symbol evidence"):
        calculate_problem_rework((one, gap))
    empty = calculate_problem_rework((one, ReworkCheckpointInput(checkpoint_id="b", symbols=())))
    assert [item.rework for item in empty.transitions] == [Decimal("1")]


def _correctness(
    count: int, checkpoint_id: str | None = None
) -> list[CheckpointCorrectnessEvidence]:
    return [
        CheckpointCorrectnessEvidence(
            checkpoint_id=checkpoint_id or f"c{index}", passed=1, total=1, rate=Decimal(1)
        )
        for index in range(count)
    ]


@pytest.mark.parametrize(
    ("correctness", "values", "message"),
    [
        (_correctness(1), ([1], [1], [1], [], []), "at least two checkpoints"),
        (_correctness(2), ([1], [1, 1], [1, 1], [1], [1]), "must match configured"),
        (_correctness(2), ([1, 1], [1, 1], [1, 1], [], [1]), "one less than checkpoints"),
        (_correctness(2, "same"), ([1, 1], [1, 1], [1, 1], [1], [1]), "must be unique"),
        (_correctness(2), ([1, 2], [1, 1], [1, 1], [1], [1]), r"finite value in \[0, 1\]"),
    ],
)
def test_problem_arithmetic_refuses_incomplete_or_out_of_range_components(
    correctness: list[CheckpointCorrectnessEvidence], values: tuple[list[int], ...], message: str
) -> None:
    series = [tuple(Decimal(value) for value in column) for column in values]
    with pytest.raises(ValueError, match=message):
        arithmetic.calculate_problem_score("p", correctness, *series)
    with pytest.raises(ValueError, match="empty"):
        arithmetic.mean(())
