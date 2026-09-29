"""ScorePolicy registry and formulas on boundary inputs (WS-A: A4, F15)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from raidar.spec.policies import (
    DEFAULT_POLICY_REGISTRY,
    SCB_CANONICAL,
    SCB_CANONICAL_ONESHOT,
    ComponentStatus,
    IneligibilityCode,
    PolicyResolver,
    ScorePolicy,
    checkpoint_correctness,
)
from raidar.spec.references import RefKind, RegistryRef, RegistryReferenceError

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
    policy = DEFAULT_POLICY_REGISTRY.default_for_checkpoints(1)

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
def test_multi_checkpoint_selects_canonical(k: int, expected: ScorePolicy) -> None:
    assert DEFAULT_POLICY_REGISTRY.default_for_checkpoints(k) is expected


@pytest.mark.parametrize(("policy", "k"), [(SCB_CANONICAL, 1), (SCB_CANONICAL_ONESHOT, 2)])
def test_policy_outside_its_k_range_is_ineligible(policy: ScorePolicy, k: int) -> None:
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


def test_checkpoint_correctness_is_solved_over_k() -> None:
    assert checkpoint_correctness(3, 8) == Decimal("0.375")
    assert checkpoint_correctness(0, 1) == Decimal("0")
    with pytest.raises(ValueError, match="solved"):
        checkpoint_correctness(9, 8)
    with pytest.raises(ValueError, match="checkpoint count"):
        DEFAULT_POLICY_REGISTRY.default_for_checkpoints(0)


def test_policy_definitions_enforce_weight_invariants() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        ScorePolicy("bad", "1", {"V": Decimal("0.5")}, min_checkpoints=1)
    with pytest.raises(ValueError, match="not_applicable"):
        ScorePolicy(
            "bad", "1", {"V": Decimal("1")}, min_checkpoints=1, not_applicable=frozenset({"V"})
        )
    with pytest.raises(ValueError, match="invalid inertia"):
        ScorePolicy("bad", "1", {"C": Decimal("1")}, min_checkpoints=1)


def test_policy_registry_and_resolver() -> None:
    resolver = PolicyResolver()
    canonical = resolver.resolve(
        RegistryRef.parse(RefKind.POLICY, "scb-canonical@1"), base_dir=Path()
    )
    oneshot = resolver.resolve(
        RegistryRef.parse(RefKind.POLICY, "scb-canonical-oneshot@1"), base_dir=Path()
    )

    assert DEFAULT_POLICY_REGISTRY.refs() == ("scb-canonical-oneshot@1", "scb-canonical@1")
    assert canonical.digest == SCB_CANONICAL.digest() != oneshot.digest
    assert canonical.metadata["inertia_weights"] == {
        "V": "0.15",
        "E": "0.15",
        "H": "0.20",
        "R": "0.30",
        "G": "0.20",
    }
    with pytest.raises(RegistryReferenceError, match="unknown score policy"):
        resolver.resolve(RegistryRef.parse(RefKind.POLICY, "scb-canonical@2"), base_dir=Path())
    with pytest.raises(ValueError, match="duplicate"):
        type(DEFAULT_POLICY_REGISTRY)((SCB_CANONICAL, SCB_CANONICAL))
