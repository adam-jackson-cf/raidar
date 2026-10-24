"""Metric registry and formulas on boundary inputs (WS-A: A4, F15)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from raidar.contract.references import RefKind, RegistryRef, RegistryReferenceError
from raidar.scoring.framework.metrics import (
    DEFAULT_METRIC_REGISTRY,
    SCB_CANONICAL,
    SCB_CANONICAL_ONESHOT,
    SCB_CANONICAL_ONESHOT_V2,
    SCB_CANONICAL_V2,
    ComponentStatus,
    IneligibilityCode,
    Metric,
    MetricResolver,
    checkpoint_pass_rate,
    checkpoint_solve_rate,
    problem_correctness,
)

FULL = {"C": 1, "V": 1, "E": 1, "H": 1, "R": 1, "G": 1}


def test_canonical_formula_matches_hand_computation() -> None:
    result = SCB_CANONICAL.score(
        checkpoints=8,
        components={"C": 0.5, "V": 0.8, "E": 0.6, "H": 1, "R": 0.5, "G": 0},
    )

    # I = .15*.8 + .15*.6 + .20*1 + .30*.5 + .20*0 = 0.56; S = 100*.5*(.70 + .30*.56)
    assert result.eligible
    assert result.inertia == Decimal("0.560000000000")
    assert result.score == Decimal("43.400000")
    assert set(result.components.values()) == {ComponentStatus.USED}


@pytest.mark.parametrize(
    ("components", "expected"),
    [
        ({**FULL, "C": 0}, Decimal("0.000000")),
        (FULL, Decimal("100.000000")),
        ({"C": 1, "V": 0, "E": 0, "H": 0, "R": 0, "G": 0}, Decimal("70.000000")),
    ],
    ids=["zero-correctness", "perfect", "zero-inertia"],
)
def test_canonical_boundaries(components: dict[str, int], expected: Decimal) -> None:
    result = SCB_CANONICAL.score(checkpoints=8, components=components)

    assert result.eligible
    assert result.score == expected


@pytest.mark.parametrize("missing", ["C", "V", "R", "G"])
def test_missing_component_makes_score_ineligible_never_zero(missing: str) -> None:
    components = {key: value for key, value in FULL.items() if key != missing}

    result = SCB_CANONICAL.score(checkpoints=8, components=components)

    assert not result.eligible
    assert result.score is None
    assert result.inertia is None
    assert result.components[missing] is ComponentStatus.MISSING
    assert [(r.code, r.component) for r in result.reasons] == [
        (IneligibilityCode.COMPONENT_MISSING, missing)
    ]


def test_scorer_ineligibility_takes_precedence_over_a_supplied_value() -> None:
    result = SCB_CANONICAL.score(
        checkpoints=8,
        components=FULL,
        ineligible={"G": ["measurement_environment_mismatch"]},
    )

    assert result.score is None
    assert result.components["G"] is ComponentStatus.INELIGIBLE
    assert result.reasons[0].detail == "measurement_environment_mismatch"


def test_k1_selects_oneshot_and_drops_r_and_g() -> None:
    policy = DEFAULT_METRIC_REGISTRY.default_for_checkpoints(1)

    result = policy.score(checkpoints=1, components={"C": 1, "V": 0.5, "E": 1, "H": 0})

    assert policy is SCB_CANONICAL_ONESHOT
    assert result.eligible
    # I1 = .30*.5 + .30*1 + .40*0 = 0.45; S = 100*(.70 + .30*.45)
    assert result.inertia == Decimal("0.450000000000")
    assert result.score == Decimal("83.500000")
    assert result.components["R"] is ComponentStatus.NOT_APPLICABLE
    assert result.components["G"] is ComponentStatus.NOT_APPLICABLE


def test_oneshot_ignores_supplied_r_and_g_values() -> None:
    base = {"C": 1, "V": 0.5, "E": 1, "H": 0}

    with_rg = SCB_CANONICAL_ONESHOT.score(checkpoints=1, components={**base, "R": 0, "G": 0})

    assert with_rg.score == SCB_CANONICAL_ONESHOT.score(checkpoints=1, components=base).score


@pytest.mark.parametrize(("k", "expected"), [(2, SCB_CANONICAL), (8, SCB_CANONICAL)])
def test_multi_checkpoint_selects_canonical(k: int, expected: Metric) -> None:
    assert DEFAULT_METRIC_REGISTRY.default_for_checkpoints(k) is expected


@pytest.mark.parametrize(("policy", "k"), [(SCB_CANONICAL, 1), (SCB_CANONICAL_ONESHOT, 2)])
def test_policy_outside_its_k_range_is_ineligible(policy: Metric, k: int) -> None:
    result = policy.score(checkpoints=k, components=FULL)

    assert result.score is None
    assert result.reasons[0].code is IneligibilityCode.CHECKPOINT_COUNT_UNSUPPORTED


@pytest.mark.parametrize(
    ("components", "error"),
    [
        ({**FULL, "V": 1.01}, ValueError),
        ({**FULL, "V": -0.1}, ValueError),
        ({**FULL, "V": float("nan")}, ValueError),
        ({**FULL, "V": True}, TypeError),
        ({**FULL, "Z": 0.5}, ValueError),
    ],
)
def test_invalid_component_values_are_rejected(components: dict, error: type) -> None:
    with pytest.raises(error):
        SCB_CANONICAL.score(checkpoints=8, components=components)


def test_checkpoint_solve_rate_is_solved_over_k() -> None:
    assert checkpoint_solve_rate(3, 8) == Decimal("0.375")
    assert checkpoint_solve_rate(0, 1) == Decimal("0")
    with pytest.raises(ValueError, match="solved"):
        checkpoint_solve_rate(9, 8)
    with pytest.raises(ValueError, match="checkpoint count"):
        DEFAULT_METRIC_REGISTRY.default_for_checkpoints(0)


def test_correctness_is_the_fork_mean_checkpoint_pass_rate_not_solved_over_k() -> None:
    # Fork C_p = sum(passed_k/total_k)/K: 0.9 and 0.5 are both unsolved checkpoints,
    # so solved/K would be 0, but C is their mean pass rate (with an unreached 0).
    rates = [checkpoint_pass_rate(9, 10), checkpoint_pass_rate(1, 2), Decimal(0)]

    # Precision 50, ROUND_HALF_EVEN, as the fork's _mean.
    assert problem_correctness(rates) == Decimal("0.4" + "6" * 48 + "7")
    assert problem_correctness([checkpoint_pass_rate(5, 5)] * 8) == Decimal("1")
    with pytest.raises(ValueError, match="graded"):
        checkpoint_pass_rate(0, 0)
    with pytest.raises(ValueError, match="at least one"):
        problem_correctness([])
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        problem_correctness([Decimal("1.1")])


QUALITY_GAPS = [
    pytest.param({"V": 1, "E": 1, "H": 1, "R": 1}, {}, id="G-missing"),
    pytest.param(FULL, {"G": ["measurement_environment_mismatch"]}, id="G-ineligible"),
    pytest.param({}, {}, id="all-quality-missing"),
]


@pytest.mark.parametrize(("quality", "ineligible"), QUALITY_GAPS)
def test_v1_is_fork_exact_c_zero_with_a_quality_gap_is_ineligible(
    quality: dict, ineligible: dict
) -> None:
    components = {**quality, "C": 0}

    result = SCB_CANONICAL.score(checkpoints=8, components=components, ineligible=ineligible)

    assert result.score is None
    assert not result.eligible
    assert not result.zero_correctness


@pytest.mark.parametrize(("quality", "ineligible"), QUALITY_GAPS)
def test_v2_c_zero_scores_zero_even_with_a_quality_gap(quality: dict, ineligible: dict) -> None:
    components = {**quality, "C": 0}

    result = SCB_CANONICAL_V2.score(checkpoints=8, components=components, ineligible=ineligible)

    assert result.eligible
    assert result.score == Decimal("0.000000")
    assert result.correctness == Decimal("0E-12")
    assert result.zero_correctness
    assert result.inertia is None
    assert result.components["C"] is ComponentStatus.USED
    assert ComponentStatus.USED not in {
        status for key, status in result.components.items() if key == "G"
    }


@pytest.mark.parametrize(("quality", "ineligible"), QUALITY_GAPS)
def test_v2_c_above_zero_with_a_quality_gap_stays_ineligible(
    quality: dict, ineligible: dict
) -> None:
    components = {**quality, "C": Decimal("0.000000000001")}

    result = SCB_CANONICAL_V2.score(checkpoints=8, components=components, ineligible=ineligible)

    assert result.score is None
    assert not result.zero_correctness
    assert {reason.code for reason in result.reasons} <= {
        IneligibilityCode.COMPONENT_MISSING,
        IneligibilityCode.COMPONENT_INELIGIBLE,
    }


def test_v2_unmeasured_c_is_still_ineligible() -> None:
    missing = SCB_CANONICAL_V2.score(checkpoints=8, components={"V": 1})
    flagged = SCB_CANONICAL_V2.score(
        checkpoints=8, components={**FULL, "C": 0}, ineligible={"C": ["verifier leak"]}
    )

    assert missing.score is None
    assert missing.components["C"] is ComponentStatus.MISSING
    assert flagged.score is None
    assert flagged.components["C"] is ComponentStatus.INELIGIBLE


def test_v2_c_zero_does_not_override_an_unsupported_k() -> None:
    result = SCB_CANONICAL_V2.score(checkpoints=1, components={"C": 0})

    assert result.score is None
    assert result.reasons[0].code is IneligibilityCode.CHECKPOINT_COUNT_UNSUPPORTED


def test_oneshot_v2_c_zero_scores_zero_without_quality() -> None:
    v1 = SCB_CANONICAL_ONESHOT.score(checkpoints=1, components={"C": 0, "V": 1})
    v2 = SCB_CANONICAL_ONESHOT_V2.score(checkpoints=1, components={"C": 0, "V": 1})

    assert v1.score is None
    assert v2.score == Decimal("0.000000")
    assert v2.components["R"] is ComponentStatus.NOT_APPLICABLE


def test_v2_matches_v1_whenever_every_component_is_measured() -> None:
    for components in (FULL, {**FULL, "C": 0}, {**FULL, "C": 0.5, "V": 0.2, "G": 0}):
        v1 = SCB_CANONICAL.score(checkpoints=8, components=components)
        v2 = SCB_CANONICAL_V2.score(checkpoints=8, components=components)
        assert (v1.score, v1.inertia, v1.correctness) == (v2.score, v2.inertia, v2.correctness)
    # Same components, different rule: the digests must differ so locks notice.
    assert SCB_CANONICAL.digest() != SCB_CANONICAL_V2.digest()
    assert SCB_CANONICAL.definition()["zero_correctness"] is None
    assert SCB_CANONICAL_V2.definition()["zero_correctness"]
    assert "passed_k/graded_k" in str(SCB_CANONICAL.definition()["correctness"])


def test_policy_definitions_enforce_weight_invariants() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        Metric("bad", "1", {"V": Decimal("0.5")}, min_checkpoints=1)
    with pytest.raises(ValueError, match="not_applicable"):
        Metric("bad", "1", {"V": Decimal("1")}, min_checkpoints=1, not_applicable=frozenset({"V"}))
    with pytest.raises(ValueError, match="invalid inertia"):
        Metric("bad", "1", {"C": Decimal("1")}, min_checkpoints=1)


def test_policy_registry_and_resolver() -> None:
    resolver = MetricResolver()
    canonical = resolver.resolve(
        RegistryRef.parse(RefKind.METRIC, "scb-canonical@1"), base_dir=Path()
    )
    oneshot = resolver.resolve(
        RegistryRef.parse(RefKind.METRIC, "scb-canonical-oneshot@1"), base_dir=Path()
    )

    assert DEFAULT_METRIC_REGISTRY.refs() == (
        "scb-canonical-oneshot@1",
        "scb-canonical-oneshot@2",
        "scb-canonical@1",
        "scb-canonical@2",
    )
    assert canonical.digest == SCB_CANONICAL.digest() != oneshot.digest
    assert canonical.metadata["inertia_weights"] == {
        "V": "0.15",
        "E": "0.15",
        "H": "0.20",
        "R": "0.30",
        "G": "0.20",
    }
    with pytest.raises(RegistryReferenceError, match="unknown metric"):
        resolver.resolve(RegistryRef.parse(RefKind.METRIC, "scb-canonical@9"), base_dir=Path())
    with pytest.raises(ValueError, match="duplicate"):
        type(DEFAULT_METRIC_REGISTRY)((SCB_CANONICAL, SCB_CANONICAL))
