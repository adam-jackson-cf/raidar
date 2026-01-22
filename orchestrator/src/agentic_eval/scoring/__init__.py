"""Scoring modules for multi-dimensional evaluation."""

from pathlib import Path

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

# Scoring weights from research document
WEIGHTS = {
    "functional": 0.40,
    "compliance": 0.25,
    "visual": 0.20,
    "efficiency": 0.15,
}


def calculate_composite_score(
    functional: FunctionalScore,
    compliance: ComplianceScore,
    visual: VisualScore | None,
    efficiency: EfficiencyScore,
) -> float:
    """Calculate weighted composite score.

    Formula: score = (functional * 0.4) + (compliance * 0.25) + (visual * 0.2) + (efficiency * 0.15)

    Args:
        functional: Functional test results
        compliance: Compliance evaluation results
        visual: Visual regression results (optional)
        efficiency: Efficiency metrics

    Returns:
        Composite score between 0 and 1
    """
    # Convert functional to 0-1 score
    functional_score = 1.0 if functional.passed else 0.0

    # Use compliance score directly
    compliance_score = compliance.score

    # Use visual similarity or 1.0 if no visual check
    visual_score = visual.similarity if visual else 1.0

    # Use efficiency score directly
    efficiency_score = efficiency.score

    # Calculate weighted sum
    if visual:
        composite = (
            functional_score * WEIGHTS["functional"]
            + compliance_score * WEIGHTS["compliance"]
            + visual_score * WEIGHTS["visual"]
            + efficiency_score * WEIGHTS["efficiency"]
        )
    else:
        # Redistribute visual weight to other dimensions
        adjusted_total = WEIGHTS["functional"] + WEIGHTS["compliance"] + WEIGHTS["efficiency"]
        composite = (
            functional_score * (WEIGHTS["functional"] / adjusted_total)
            + compliance_score * (WEIGHTS["compliance"] / adjusted_total)
            + efficiency_score * (WEIGHTS["efficiency"] / adjusted_total)
        )

    return round(composite, 3)


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

    # Calculate composite
    composite = calculate_composite_score(functional, compliance, visual, efficiency)

    return Scorecard(
        functional=functional,
        compliance=compliance,
        visual=visual,
        efficiency=efficiency,
        composite=composite,
        scaffold_audit=scaffold_audit,
    )


__all__ = [
    "evaluate_functional",
    "evaluate_compliance",
    "evaluate_visual",
    "evaluate_efficiency",
    "evaluate_all",
    "calculate_composite_score",
    "WEIGHTS",
]
