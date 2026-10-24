"""What analysis and the review projections read from the quality, gate and process scorers.

- the quality slot reads ``code-quality@3``, and the headline names it as the scorer it needs;
- the gate and process outcomes are the metrics ``gate-effort`` and ``process-compliance`` report;
- every compared metric names its better direction (smoke-04 O6).
"""

from __future__ import annotations

from raidar.design.experiments.metrics import DEFAULT_METRIC_REGISTRY
from raidar.evaluation.analysis.markdown import BETTER_LEGEND, _better
from raidar.evaluation.analysis.outcomes import (
    GATE_METRICS,
    GATE_PREFIX,
    PROCESS_METRICS,
    PROCESS_PREFIX,
)
from raidar.evaluation.review.projection.headline import needed_scorers
from raidar.scoring.framework.builtin_manifests import CODE_QUALITY
from raidar.scoring.gate_effort.manifest import GATE_EFFORT
from raidar.scoring.process_checks.manifest import PROCESS_COMPLIANCE

GATE_ONLY_ADDED_LATER = {
    "fix_round_correctness_delta",
    "total_new_first_run_blocking",
    "final_step_end_blocking",
    "total_first_run_untyped_call",
    "final_step_end_untyped_call",
    "first_run_tests_failed_rate",
    "first_run_coverage_met_rate",
    "total_first_run_failed_tests",
    "first_pass_clean_except_autofix_rate",
    "first_pass_clean_except_tests_rate",
    "first_pass_clean_except_autofix_tests_rate",
    "total_new_first_run_blocking_ex_cascade",
    "final_step_end_blocking_ex_cascade",
    "total_new_first_run_blocking_net",
    "final_step_end_blocking_net",
}


def test_the_headline_needs_the_code_quality_scorer() -> None:
    policy = DEFAULT_METRIC_REGISTRY.get("scb-canonical@2")

    assert (
        needed_scorers(policy)
        == ("correctness@1", CODE_QUALITY.ref)
        == (
            "correctness@1",
            "code-quality@3",
        )
    )


def test_gate_outcomes_are_the_metrics_gate_effort_reports() -> None:
    compared = {metric.id.removeprefix(GATE_PREFIX) for metric in GATE_METRICS}
    reported = {metric.id for metric in GATE_EFFORT.metrics}

    assert GATE_EFFORT.ref == "gate-effort@10"
    assert compared >= GATE_ONLY_ADDED_LATER
    assert compared <= reported


def test_process_outcomes_are_the_trajectory_metrics_process_compliance_reports() -> None:
    compared = {m.id.removeprefix(PROCESS_PREFIX) for m in PROCESS_METRICS}

    assert PROCESS_COMPLIANCE.ref == "process-compliance@6"
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
    assert _better("gate.total_new_first_run_blocking") == "↓ lower"
    assert _better("gate.first_pass_clean_except_autofix_tests_rate") == "↑ higher"
    assert _better("gate.final_step_end_blocking_ex_cascade") == "↓ lower"
    assert _better("gate.total_new_first_run_blocking_net") == "↓ lower"
    assert _better("gate.final_step_end_blocking_net") == "↓ lower"
    assert _better("erosion") == "↑ higher"
    assert _better("process.total_test_commands_not_run") == "↓ lower"
    assert _better("process.work_session_tests_run_rate") == "↑ higher"
    assert _better("process.work_session_tests_run_any_rate") == "↑ higher"
    assert _better("process.work_session_tests_after_last_code_edit_rate") == "↑ higher"
    assert "erosion E is 1 minus the share of code mass in complex functions" in BETTER_LEGEND
