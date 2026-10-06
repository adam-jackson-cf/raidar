"""What analysis and the review projections read from iteration 3b.2's new scorer versions.

- the quality slot reads ``code-quality@2`` when a lock declares it (it reports
  ``code-quality@1``'s metrics), and the headline names it as the scorer it needs;
- the gate outcomes of ``gate-effort@6`` and the process outcomes of
  ``process-compliance@4`` are compared only for those versions;
- every compared metric names its better direction (smoke-04 O6).
"""

from __future__ import annotations

from raidar.analysis.markdown import BETTER_LEGEND, _better
from raidar.analysis.outcomes import (
    GATE_PREFIX,
    PROCESS_PREFIX,
    gate_metrics_for,
    process_metrics_for,
)
from raidar.review_projection.assessments import TrialAssessments
from raidar.review_projection.headline import needed_scorers, quality_scorer
from raidar.scorers.code_quality_refs import CODE_QUALITY_REFS, code_quality_ref
from raidar.spec.metrics import DEFAULT_METRIC_REGISTRY

V6_ONLY = {
    "total_new_first_run_blocking",
    "final_step_end_blocking",
    "total_first_run_untyped_call",
    "final_step_end_untyped_call",
    "first_run_tests_failed_rate",
    "first_run_coverage_met_rate",
    "total_first_run_failed_tests",
}


def test_the_newest_declared_code_quality_version_is_read() -> None:
    assert CODE_QUALITY_REFS == ("code-quality@2", "code-quality@1")
    assert code_quality_ref(["correctness@1", "code-quality@2"]) == "code-quality@2"
    assert code_quality_ref(["correctness@1", "code-quality@1"]) == "code-quality@1"
    assert code_quality_ref(["correctness@1"]) == "code-quality@1"


def test_the_headline_needs_the_declared_code_quality_version() -> None:
    policy = DEFAULT_METRIC_REGISTRY.get("scb-canonical@2")

    assert needed_scorers(policy, ["correctness@1", "code-quality@2"]) == (
        "correctness@1",
        "code-quality@2",
    )
    assert needed_scorers(policy) == ("correctness@1", "code-quality@1")
    assert quality_scorer(TrialAssessments(missing={"code-quality@2": "x"})) == "code-quality@2"
    assert quality_scorer(TrialAssessments()) == "code-quality@1"


def test_gate_effort_v6_outcomes_are_compared_only_for_v6() -> None:
    v6 = {metric.id.removeprefix(GATE_PREFIX) for metric in gate_metrics_for("gate-effort@6")}
    v5 = {metric.id.removeprefix(GATE_PREFIX) for metric in gate_metrics_for("gate-effort@5")}
    v3 = {metric.id.removeprefix(GATE_PREFIX) for metric in gate_metrics_for("gate-effort@3")}

    assert v6 - v5 == V6_ONLY
    assert not V6_ONLY & v3
    assert "fix_round_correctness_delta" in v5 - v3


def test_process_compliance_v4_adds_the_rate_and_the_not_run_count() -> None:
    v4 = {m.id.removeprefix(PROCESS_PREFIX) for m in process_metrics_for("process-compliance@4")}
    v3 = {m.id.removeprefix(PROCESS_PREFIX) for m in process_metrics_for("process-compliance@3")}

    assert v3 == {"stall_rate"}
    assert v4 == {"stall_rate", "compliance_rate", "total_test_commands_not_run"}
    assert process_metrics_for("process-compliance@2") == ()


def test_every_compared_metric_names_its_better_direction() -> None:
    assert _better("gate.first_pass_clean_rate") == "↑ higher"
    assert _better("gate.total_new_first_run_blocking") == "↓ lower"
    assert _better("erosion") == "↑ higher"
    assert _better("process.total_test_commands_not_run") == "↓ lower"
    assert "erosion E is 1 minus the share of code mass in complex functions" in BETTER_LEGEND
