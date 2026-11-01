"""What analysis and the review projections read from the quality, gate and process scorers.

- the quality slot reads the code-quality scorer, and the headline names it as the scorer it needs;
- the gate and process outcomes are the metrics ``verification-effort`` and
  ``process-compliance`` report;
- every compared metric names its better direction.
"""

from __future__ import annotations

import pytest

from raidar.evaluation.analysis.markdown import _better
from raidar.evaluation.review.projection.headline import needed_scorers
from raidar.scoring.catalog.outcomes import (
    GATE_METRICS,
    GATE_PREFIX,
    PRIMARY_FROM_LOCK,
    PROCESS_METRICS,
    PROCESS_PREFIX,
    SOLVE_RATE,
    SOLVE_RATE_SOLVABLE,
    primary_metric,
    primary_source,
)
from raidar.scoring.framework.builtin_manifests import CODE_QUALITY
from raidar.scoring.framework.metrics import DEFAULT_METRIC_REGISTRY
from raidar.scoring.process_checks.manifest import PROCESS_COMPLIANCE
from raidar.scoring.verification_effort.manifest import VERIFICATION_EFFORT


def test_the_headline_needs_the_code_quality_scorer() -> None:
    policy = DEFAULT_METRIC_REGISTRY.get("scb-canonical@2")

    assert CODE_QUALITY.ref in needed_scorers(policy)


def test_gate_outcomes_are_the_metrics_verification_effort_reports() -> None:
    compared = {metric.id.removeprefix(GATE_PREFIX) for metric in GATE_METRICS}
    reported = {metric.id for metric in VERIFICATION_EFFORT.metrics}

    assert compared
    assert compared <= reported


def test_process_outcomes_are_the_trajectory_metrics_process_compliance_reports() -> None:
    compared = {m.id.removeprefix(PROCESS_PREFIX) for m in PROCESS_METRICS}

    assert compared
    assert compared <= {metric.id for metric in PROCESS_COMPLIANCE.metrics}


def test_every_compared_metric_names_its_better_direction() -> None:
    compared = [metric.id for metric in (*GATE_METRICS, *PROCESS_METRICS)]
    assert all(_better(metric) != "—" for metric in compared)
    # Counts of effort and failures improve downwards, rates of success upwards.
    assert _better("gate.total_first_run_blocking") == "↓ lower"
    assert _better("gate.first_pass_clean_rate") == "↑ higher"
    assert _better("process.total_test_commands_not_run") == "↓ lower"
    assert _better("process.work_session_tests_run_rate") == "↑ higher"
    assert _better("erosion") == "↑ higher"


def test_the_primary_outcome_is_the_configured_one_else_the_solvable_solve_rate() -> None:
    assert primary_metric("continue", SOLVE_RATE) == SOLVE_RATE
    for progression in ("continue", "halt_on_failure"):
        assert primary_metric(progression) == SOLVE_RATE_SOLVABLE
    with pytest.raises(ValueError, match="no primary outcome"):
        primary_metric("rewind")
    with pytest.raises(ValueError, match="unknown outcome metric"):
        primary_metric("continue", "no_such_metric")
    assert primary_source("continue", SOLVE_RATE) == PRIMARY_FROM_LOCK
    assert "continue" in primary_source("continue")
