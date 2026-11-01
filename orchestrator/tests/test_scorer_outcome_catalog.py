"""What analysis and the review projections read from the quality, gate and process scorers.

- the quality slot reads the code-quality scorer, and the headline names it as the scorer it needs;
- the gate and process outcomes are the metrics ``verification-effort`` and
  ``process-compliance`` report;
- every compared metric names its better direction.
"""

from __future__ import annotations

from raidar.evaluation.analysis.markdown import BETTER_LEGEND, _better
from raidar.evaluation.review.projection.headline import needed_scorers
from raidar.scoring.catalog.outcomes import (
    GATE_METRICS,
    GATE_PREFIX,
    PROCESS_METRICS,
    PROCESS_PREFIX,
)
from raidar.scoring.framework.builtin_manifests import CODE_QUALITY
from raidar.scoring.framework.metrics import DEFAULT_METRIC_REGISTRY
from raidar.scoring.process_checks.manifest import PROCESS_COMPLIANCE
from raidar.scoring.verification_effort.manifest import VERIFICATION_EFFORT

GATE_OUTCOMES = {
    "first_pass_clean_rate",
    "clean_at_step_end_rate",
    "unresolved_rate",
    "gate_error_rate",
    "mean_rounds_to_clean",
    "total_fix_rounds",
    "total_first_run_blocking",
    "total_blocking_checks",
    "total_fix_input_tokens",
    "total_fix_output_tokens",
    "total_fix_cost_usd",
    "total_fix_wall_s",
    "total_gate_wall_s",
    "fix_round_correctness_delta",
}


def test_the_headline_needs_the_code_quality_scorer() -> None:
    policy = DEFAULT_METRIC_REGISTRY.get("scb-canonical@2")

    assert CODE_QUALITY.ref in needed_scorers(policy)


def test_gate_outcomes_are_the_metrics_verification_effort_reports() -> None:
    compared = {metric.id.removeprefix(GATE_PREFIX) for metric in GATE_METRICS}
    reported = {metric.id for metric in VERIFICATION_EFFORT.metrics}

    assert compared == GATE_OUTCOMES
    assert compared <= reported


def test_process_outcomes_are_the_trajectory_metrics_process_compliance_reports() -> None:
    compared = {m.id.removeprefix(PROCESS_PREFIX) for m in PROCESS_METRICS}

    assert compared == {
        "stall_rate",
        "compliance_rate",
        "total_test_commands_not_run",
        "work_session_tests_run_rate",
        "work_session_tests_run_any_rate",
        "work_session_tests_after_last_code_edit_rate",
    }
    assert compared <= {metric.id for metric in PROCESS_COMPLIANCE.metrics}


def test_every_compared_metric_names_its_better_direction() -> None:
    assert _better("gate.first_pass_clean_rate") == "↑ higher"
    assert _better("gate.total_first_run_blocking") == "↓ lower"
    assert _better("gate.total_blocking_checks") == "↓ lower"
    assert _better("gate.clean_at_step_end_rate") == "↑ higher"
    assert _better("gate.fix_round_correctness_delta") == "↑ higher"
    assert _better("erosion") == "↑ higher"
    assert _better("process.total_test_commands_not_run") == "↓ lower"
    assert _better("process.work_session_tests_run_rate") == "↑ higher"
    assert _better("process.work_session_tests_run_any_rate") == "↑ higher"
    assert _better("process.work_session_tests_after_last_code_edit_rate") == "↑ higher"
    assert "erosion E is 1 minus the share of code mass in complex functions" in BETTER_LEGEND
