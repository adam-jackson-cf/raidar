"""Factor guard (A3): arms may differ only in the declared factors.

Each arm expands to its nouns: ``condition`` (the effective component list,
``shared_components`` included, compared by content, not by condition id) and
the agent fields ``agent.<field>`` (compared by value, not by agent id). A
declared factor covers a noun when it equals the noun or is its parent
(``agent`` covers ``agent.model``). Every arm pair is compared, so the guard
rejects an experiment before any trial is planned. Shared components are
identical in every arm by construction, so they never make two arms differ.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from raidar.spec.canonical import canonical_json_bytes
from raidar.spec.schema import ArmSpec, ExperimentSpec

AGENT_NOUN_FIELDS = ("harness", "provider", "model", "reasoning_effort", "version")


@dataclass(frozen=True)
class FactorViolation:
    arm_index: int
    arm: str
    other_arm: str
    noun: str
    declared: tuple[str, ...]

    @property
    def loc(self) -> str:
        return f"arms.{self.arm_index}"

    @property
    def message(self) -> str:
        declared = ", ".join(self.declared) or "none"
        return (
            f"arms {self.other_arm!r} and {self.arm!r} differ in undeclared noun "
            f"{self.noun!r} (declared factors: {declared})"
        )


def arm_nouns(spec: ExperimentSpec, arm: ArmSpec) -> dict[str, bytes]:
    """Canonical value of every noun an arm is made of."""
    agent = spec.agent(arm.agent).model_dump(mode="json")
    condition = spec.condition(arm.condition).model_dump(mode="json", by_alias=True)
    nouns = {f"agent.{name}": canonical_json_bytes(agent[name]) for name in AGENT_NOUN_FIELDS}
    nouns["condition"] = canonical_json_bytes(condition["components"])
    return nouns


def _covered(noun: str, factors: tuple[str, ...]) -> bool:
    return any(noun == factor or noun.startswith(f"{factor}.") for factor in factors)


def check_factors(spec: ExperimentSpec) -> list[FactorViolation]:
    """Return one violation per (arm pair, undeclared differing noun)."""
    expanded = [(index, arm, arm_nouns(spec, arm)) for index, arm in enumerate(spec.arms)]
    violations: list[FactorViolation] = []
    for (_, first, first_nouns), (index, second, second_nouns) in combinations(expanded, 2):
        for noun in sorted(first_nouns):
            if first_nouns[noun] == second_nouns[noun] or _covered(noun, spec.factors):
                continue
            violations.append(
                FactorViolation(
                    arm_index=index,
                    arm=second.id,
                    other_arm=first.id,
                    noun=noun,
                    declared=spec.factors,
                )
            )
    return violations
