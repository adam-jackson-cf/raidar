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
from raidar.review.projection.assessments import TrialAssessments
from raidar.review.projection.headline import needed_scorers, quality_scorer
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
    assert CODE_QUALITY_REFS == ("code-quality@3", "code-quality@2", "code-quality@1")
    assert code_quality_ref(["code-quality@2", "code-quality@3"]) == "code-quality@3"
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


# ---- iteration 3b.3: gate-effort@7 and process-compliance@5 ------------------------------

V7_ONLY = {
    "first_pass_clean_except_autofix_rate",
    "first_pass_clean_except_tests_rate",
    "first_pass_clean_except_autofix_tests_rate",
    "total_new_first_run_blocking_ex_cascade",
    "final_step_end_blocking_ex_cascade",
}


def test_gate_effort_v7_outcomes_are_compared_only_for_v7() -> None:
    v7 = {metric.id.removeprefix(GATE_PREFIX) for metric in gate_metrics_for("gate-effort@7")}
    v6 = {metric.id.removeprefix(GATE_PREFIX) for metric in gate_metrics_for("gate-effort@6")}
    v5 = {metric.id.removeprefix(GATE_PREFIX) for metric in gate_metrics_for("gate-effort@5")}

    assert v7 - v6 == V7_ONLY
    assert v6 - v5 == V6_ONLY
    assert not V7_ONLY & v5
    assert _better("gate.first_pass_clean_except_autofix_tests_rate") == "↑ higher"
    assert _better("gate.final_step_end_blocking_ex_cascade") == "↓ lower"


def test_process_compliance_v5_adds_the_work_session_rate() -> None:
    v5 = {m.id.removeprefix(PROCESS_PREFIX) for m in process_metrics_for("process-compliance@5")}
    v4 = {m.id.removeprefix(PROCESS_PREFIX) for m in process_metrics_for("process-compliance@4")}

    assert v5 - v4 == {"work_session_tests_run_rate"}
    assert _better("process.work_session_tests_run_rate") == "↑ higher"


# ---- iteration 3b.4: gate-effort@8 and process-compliance@6 ------------------------------

V8_ONLY = {"total_new_first_run_blocking_net", "final_step_end_blocking_net"}


def test_gate_effort_v8_outcomes_are_compared_only_for_v8() -> None:
    v8 = {metric.id.removeprefix(GATE_PREFIX) for metric in gate_metrics_for("gate-effort@8")}
    v7 = {metric.id.removeprefix(GATE_PREFIX) for metric in gate_metrics_for("gate-effort@7")}
    v6 = {metric.id.removeprefix(GATE_PREFIX) for metric in gate_metrics_for("gate-effort@6")}

    assert v8 - v7 == V8_ONLY
    assert v7 - v6 == V7_ONLY
    assert not V8_ONLY & v6
    assert _better("gate.total_new_first_run_blocking_net") == "↓ lower"
    assert _better("gate.final_step_end_blocking_net") == "↓ lower"


def test_process_compliance_v6_adds_the_any_and_after_code_edit_rates() -> None:
    v6 = {m.id.removeprefix(PROCESS_PREFIX) for m in process_metrics_for("process-compliance@6")}
    v5 = {m.id.removeprefix(PROCESS_PREFIX) for m in process_metrics_for("process-compliance@5")}

    assert v6 - v5 == {
        "work_session_tests_run_any_rate",
        "work_session_tests_after_last_code_edit_rate",
    }
    assert _better("process.work_session_tests_run_any_rate") == "↑ higher"
    assert _better("process.work_session_tests_after_last_code_edit_rate") == "↑ higher"
