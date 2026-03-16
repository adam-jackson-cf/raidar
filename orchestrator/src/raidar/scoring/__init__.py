"""Scoring modules for multi-dimensional evaluation."""

from pathlib import Path

from ..config import settings
from ..schemas.events import GateEvent
from ..schemas.scenario import AcceptanceConfig, VisualConfig
from ..schemas.scorecard import (
    AcceptanceScore,
    FunctionalScore,
    Scorecard,
    VerificationStabilityScore,
    VisualScore,
)
from .acceptance import evaluate_acceptance
from .functional import evaluate_functional
from .verification_stability import evaluate_verification_stability
from .visual import evaluate_visual


def get_weights() -> dict[str, float]:
    """Get scoring weights from config."""

    return {
        "functional": settings.weights.functional,
        "acceptance": settings.weights.acceptance,
        "visual": settings.weights.visual,
        "verification_stability": settings.weights.verification_stability,
    }


WEIGHTS = get_weights()


def evaluate_all(
    workspace: Path,
    acceptance_config: AcceptanceConfig,
    visual_config: VisualConfig | None,
    gate_events: list[GateEvent],
    rules_path: Path | None = None,
    run_llm_checks: bool = True,
) -> Scorecard:
    """Run all evaluations and return complete scorecard."""

    functional = evaluate_functional(workspace)
    acceptance = evaluate_acceptance(workspace, acceptance_config, rules_path, run_llm_checks)

    visual = None
    if visual_config:
        reference_path = workspace.parent / visual_config.reference_image
        visual = evaluate_visual(
            workspace=workspace,
            reference_image=reference_path,
            screenshot_command=visual_config.screenshot_command,
        )

    verification_stability = evaluate_verification_stability(gate_events)

    return Scorecard(
        functional=functional,
        acceptance=acceptance,
        visual=visual,
        verification_stability=verification_stability,
    )


__all__ = [
    "evaluate_functional",
    "evaluate_acceptance",
    "evaluate_visual",
    "evaluate_verification_stability",
    "evaluate_all",
    "WEIGHTS",
    "get_weights",
    "AcceptanceScore",
    "VerificationStabilityScore",
    "FunctionalScore",
    "Scorecard",
    "VisualScore",
]
