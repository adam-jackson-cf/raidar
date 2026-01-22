"""Scoring modules for multi-dimensional evaluation."""

from pathlib import Path

from ..config import settings
from ..schemas.events import GateEvent
from ..schemas.scorecard import (
    ComplianceScore,
    EfficiencyScore,
    FunctionalScore,
    Scorecard,
    VisualScore,
)
from ..schemas.task import ComplianceConfig, VisualConfig
from .compliance import evaluate_compliance
from .efficiency import evaluate_efficiency
from .functional import evaluate_functional
from .visual import evaluate_visual


def get_weights() -> dict[str, float]:
    """Get scoring weights from config."""
    return {
        "functional": settings.weights.functional,
        "compliance": settings.weights.compliance,
        "visual": settings.weights.visual,
        "efficiency": settings.weights.efficiency,
    }


# For backward compatibility - use get_weights() for dynamic access
WEIGHTS = get_weights()


def evaluate_all(
    workspace: Path,
    compliance_config: ComplianceConfig,
    visual_config: VisualConfig | None,
    gate_events: list[GateEvent],
    rules_path: Path | None = None,
    run_llm_checks: bool = True,
    baseline_manifest_path: Path | None = None,
) -> Scorecard:
    """Run all evaluations and return complete scorecard.

    Args:
        workspace: Path to workspace directory
        compliance_config: Compliance configuration from task
        visual_config: Visual configuration from task (optional)
        gate_events: Gate events from execution
        rules_path: Path to rules file for LLM context
        run_llm_checks: Whether to run LLM judge checks
        baseline_manifest_path: Path to baseline scaffold manifest for audit

    Returns:
        Complete Scorecard with all dimensions
    """
    from ..audit.scaffold_manifest import create_scaffold_audit, load_manifest

    # Evaluate each dimension
    functional = evaluate_functional(workspace)
    compliance = evaluate_compliance(
        workspace, compliance_config, rules_path, run_llm_checks
    )

    visual = None
    if visual_config:
        reference_path = workspace.parent / visual_config.reference_image
        visual = evaluate_visual(
            workspace=workspace,
            reference_image=reference_path,
            screenshot_command=visual_config.screenshot_command,
            threshold=visual_config.threshold,
        )

    efficiency = evaluate_efficiency(gate_events)

    # Generate scaffold audit if baseline manifest provided
    scaffold_audit = None
    if baseline_manifest_path and baseline_manifest_path.exists():
        baseline_manifest = load_manifest(baseline_manifest_path)
        scaffold_audit = create_scaffold_audit(baseline_manifest, workspace)

    return Scorecard(
        functional=functional,
        compliance=compliance,
        visual=visual,
        efficiency=efficiency,
        scaffold_audit=scaffold_audit,
    )


__all__ = [
    "evaluate_functional",
    "evaluate_compliance",
    "evaluate_visual",
    "evaluate_efficiency",
    "evaluate_all",
    "WEIGHTS",
    "get_weights",
    "ComplianceScore",
    "EfficiencyScore",
    "FunctionalScore",
    "Scorecard",
    "VisualScore",
]
