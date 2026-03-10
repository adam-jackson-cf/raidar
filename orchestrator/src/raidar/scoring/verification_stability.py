"""Verification-stability scoring based on gate failures and repetition."""

from ..schemas.events import GateEvent
from ..schemas.scorecard import VerificationStabilityScore


def calculate_verification_stability_score(
    total_failures: int,
    unique_categories: int,
    repeat_failures: int,
    max_failures: int = 4,
) -> float:
    """Calculate verification-stability score based on failures."""

    del unique_categories
    base_penalty = total_failures / max_failures
    repeat_penalty = repeat_failures * 0.2
    score = max(0.0, 1.0 - base_penalty - repeat_penalty)
    return round(score, 3)


def evaluate_verification_stability(
    gate_events: list[GateEvent],
) -> VerificationStabilityScore:
    """Evaluate verification stability from gate execution history."""

    total_failures = sum(1 for event in gate_events if event.exit_code != 0)
    categories_seen: set[str] = set()
    repeat_failures = 0

    for event in gate_events:
        if event.exit_code != 0 and event.failure_category:
            if event.failure_category in categories_seen:
                repeat_failures += 1
            categories_seen.add(event.failure_category)

    return VerificationStabilityScore(
        total_gate_failures=total_failures,
        unique_failure_categories=len(categories_seen),
        repeat_failures=repeat_failures,
    )
