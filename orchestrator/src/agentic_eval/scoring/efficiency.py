"""Efficiency scoring based on gate failures and repetition."""

from ..schemas.events import GateEvent
from ..schemas.scorecard import EfficiencyScore


def calculate_efficiency_score(
    total_failures: int,
    unique_categories: int,
    repeat_failures: int,
    max_failures: int = 4,
) -> float:
    """Calculate efficiency score based on failures.

    Formula: efficiency = max(0, 1 - (gate_failures / max_failures) - (repeat_failures * 0.2))

    Args:
        total_failures: Total number of gate failures
        unique_categories: Number of unique failure categories
        repeat_failures: Number of repeated failures (same category)
        max_failures: Maximum failures for normalization

    Returns:
        Efficiency score between 0 and 1
    """
    base_penalty = total_failures / max_failures
    repeat_penalty = repeat_failures * 0.2
    score = max(0.0, 1.0 - base_penalty - repeat_penalty)
    return round(score, 3)


def evaluate_efficiency(gate_events: list[GateEvent]) -> EfficiencyScore:
    """Evaluate efficiency from gate execution history.

    Args:
        gate_events: List of gate events from execution

    Returns:
        EfficiencyScore with failure metrics
    """
    total_failures = sum(1 for e in gate_events if e.exit_code != 0)

    categories_seen: set[str] = set()
    repeat_failures = 0

    for event in gate_events:
        if event.exit_code != 0 and event.failure_category:
            if event.failure_category in categories_seen:
                repeat_failures += 1
            categories_seen.add(event.failure_category)

    unique_categories = len(categories_seen)
    score = calculate_efficiency_score(
        total_failures=total_failures,
        unique_categories=unique_categories,
        repeat_failures=repeat_failures,
    )

    return EfficiencyScore(
        total_gate_failures=total_failures,
        unique_failure_categories=unique_categories,
        repeat_failures=repeat_failures,
        score=score,
    )
