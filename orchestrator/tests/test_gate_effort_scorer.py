"""gate-effort: the effort a guardrail-gate commit gate cost, read from its records.

Unit cases build each step's agent log dir by hand (the gate record, the run
logs in ``guardrail-check``'s printed format and the per-run JSON reports); the
end-to-end case runs the real wrapper with a scripted agent and a gate that
writes its report to ``$RAIDAR_GATE_REPORT_DIR``, then scores the logs it left.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, AssessStatus
from raidar.scoring.gate_effort import GATE_EFFORT, gate_effort_scorer
from raidar.scoring.gate_effort.evidence import run_summary
from raidar.scoring.gate_effort.families import FAMILIES, family_counts, family_of
from raidar.scoring.gate_effort.records import (
    RECORD_PATH,
    GateRecordError,
    parse_output,
    read_step_gate,
)
from tests.gate_effort_support import (
    CAPPED,
    GUARDRAIL_STYLE_GATE,
    PASSED,
    REFUSED,
    _assess,
    _finding,
    _run_record,
    _stage,
    _step,
    _trace,
    _value,
    _values,
    _write_gate,
)
from tests.test_guardrail_gate_workflow import ScriptedInnerAgent, _agent, _run
from tests.test_guardrail_gate_workflow import _reset_script as _reset_script  # noqa: F401
from tests.test_guardrail_gate_workflow import shims as shims  # noqa: F401 (fixture)

# ---- families and parsing ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "family"),
    [
        ("C901", "complexity"),
        ("PLR0912", "complexity"),
        ("PYS250", "complexity"),
        ("PLR0904", "structure"),
        ("PYQ200", "structure"),
        ("FANOUT.general", "structure"),
        ("PARITY.lock-version", "structure"),
        ("MYPY.arg-type", "typing"),
        ("F841", "defects"),
        ("B904", "defects"),
        ("E999", "defects"),
        ("PYS000", "defects"),
        ("PYQ299", "defects"),
        ("E402", "style"),
        ("C401", "style"),
        ("SIM114", "style"),
        ("FORMAT", "style"),
        ("UP017", "style"),
        ("N802", "naming"),
        ("A001", "naming"),
        ("PYQ104", "naming"),
        ("TST001", "tests"),
        ("TEST.missing", "tests"),
        ("TEST.coverage-minimum", "tests"),
        ("PT011", "tests"),
        ("PYQ102", "tests"),
        ("SUPPRESS", "suppressions"),
        ("PGH003", "suppressions"),
        ("RUF100", "suppressions"),
        ("RUF012", "other"),
        ("XYZ", "other"),
        ("ARG001", "other"),
    ],
)
def test_every_code_maps_to_one_family(code: str, family: str) -> None:
    assert family_of(code) == family


def test_family_counts_name_every_family() -> None:
    counts = family_counts(["C901", "C901", "MYPY.x"])
    assert list(counts) == list(FAMILIES)
    assert (counts["complexity"], counts["typing"], counts["style"]) == (2, 1, 0)


def test_the_printed_listing_is_complete_only_when_every_blocking_finding_is_listed() -> None:
    refused = parse_output(REFUSED)
    assert refused is not None
    assert (refused.total, refused.codes) == (3, ("C901", "MYPY.no-untyped-def", "I001"))
    passed = parse_output(PASSED)
    assert passed is not None
    assert (passed.total, passed.codes) == (0, ())
    capped = parse_output(CAPPED)
    assert capped is not None
    assert (capped.total, capped.listed, capped.complete) == (61, 60, False)
    assert parse_output("make: *** [check] Error 1\n") is None


# ---- the scorer --------------------------------------------------------------------------


def test_an_arm_without_the_gate_is_not_applicable_never_zero(tmp_path: Path) -> None:
    result = _assess(_step(tmp_path, "checkpoint_1"), _step(tmp_path, "checkpoint_2"))

    assert result.status is AssessStatus.NOT_APPLICABLE
    assert {item.status for item in result.observations} == {AssessStatus.NOT_APPLICABLE}
    assert all(item.value is None for item in result.observations)
    assert "without a commit gate" in (result.reason or "")


def test_a_gated_trajectory_reports_rounds_findings_by_family_and_fix_cost(
    tmp_path: Path,
) -> None:
    first, second = _step(tmp_path, "checkpoint_1"), _step(tmp_path, "checkpoint_2")
    # Step 1: refused (the JSON report is authoritative), then clean after one fix.
    _write_gate(
        _trace(first),
        outputs=[REFUSED, PASSED],
        termination="gate_clean",
        signal=True,
        reports={
            1: [_finding("C901"), _finding("C901"), _finding("TST001"), _finding("D1", "warn")]
        },
    )
    # Step 2: clean on the first run.
    _write_gate(_trace(second), outputs=[PASSED], termination="gate_clean", signal=True)

    result = _assess(first, second)

    def value(metric: str, subject: str = "checkpoint_1") -> float | None:
        return _value(result, metric, subject)

    assert result.status is AssessStatus.OK
    assert (value("gate_runs"), value("fix_rounds"), value("rounds_to_clean")) == (2, 1, 1)
    assert (value("first_pass_clean"), value("first_pass_clean", "checkpoint_2")) == (0, 1)
    assert (value("unresolved"), value("gate_error"), value("gate_clean_at_step_end")) == (0, 0, 1)
    # The report wins over the printed listing: 3 blocking, warnings excluded.
    assert value("first_run_blocking") == 3
    assert value("first_run_blocking.complexity") == 2
    assert value("first_run_blocking.tests") == 1
    assert value("first_run_blocking.typing") == 0
    assert value("blocking_findings") == 3
    assert value("rounds_to_clean", "checkpoint_2") == 0
    assert (value("fix_input_tokens"), value("fix_cost_usd"), value("fix_wall_s")) == (
        1000,
        0.01,
        30.0,
    )
    assert value("gate_wall_s") == 1.0
    assert value("fix_cost_usd", "checkpoint_2") == 0
    rate = _values(result)[("first_pass_clean_rate", TRAJECTORY_SUBJECT)]
    assert (rate.value, rate.numerator, rate.denominator) == (0.5, 1, 2)
    assert value("mean_rounds_to_clean", TRAJECTORY_SUBJECT) == 0.5
    assert value("total_fix_rounds", TRAJECTORY_SUBJECT) == 1
    assert value("total_blocking_findings.complexity", TRAJECTORY_SUBJECT) == 2
    assert value("gated_steps", TRAJECTORY_SUBJECT) == 2
    messages = [item.message for item in result.evidence]
    assert messages[0] == "run 1 after stage_1_work: refused, 3 blocking (complexity 2, tests 1)"
    assert messages[1:] == ["run 2 after stage_2_fix: passed", "run 1 after stage_1_work: passed"]
    assert all(item.path.startswith("guardrail-gate/gate_run_") for item in result.evidence)


def test_the_printed_listing_counts_families_when_no_report_was_written(tmp_path: Path) -> None:
    step = _step(tmp_path, "checkpoint_1")
    assert step.trace_dir is not None
    _write_gate(step.trace_dir, outputs=[REFUSED, PASSED], termination="gate_clean", signal=True)

    result = _assess(step)

    assert _value(result, "first_run_blocking.complexity", "checkpoint_1") == 1
    assert _value(result, "first_run_blocking.typing", "checkpoint_1") == 1
    assert _value(result, "first_run_blocking.style", "checkpoint_1") == 1


def test_unresolved_gate_error_and_failed_steps_are_statused_not_zeroed(tmp_path: Path) -> None:
    exhausted, errored, failed = (_step(tmp_path, f"checkpoint_{n}") for n in (1, 2, 3))
    unreached = _step(tmp_path, "checkpoint_4", reached=False)
    _write_gate(
        _trace(exhausted),
        outputs=[CAPPED, CAPPED, CAPPED, CAPPED],
        termination="rounds_exhausted",
        signal=False,
    )
    _write_gate(
        _trace(errored),
        outputs=["boom\n"],
        termination="gate_error",
        signal=None,
        runs=[_run_record(1, "stage_1_work", passed=False, error="not runnable (exit 126)")],
        error="not runnable (exit 126)",
    )
    _write_gate(
        _trace(failed),
        outputs=[],
        termination="stage_failed",
        signal=None,
        stages=[_stage(1, "work", 0)],
        error="RuntimeError: harness crashed",
    )

    result = _assess(exhausted, errored, failed, unreached)
    values = _values(result)

    def status(metric: str, subject: str) -> AssessStatus:
        return values[(metric, subject)].status

    assert _value(result, "unresolved", "checkpoint_1") == 1
    assert _value(result, "fix_rounds", "checkpoint_1") == 3
    assert status("rounds_to_clean", "checkpoint_1") is AssessStatus.NOT_APPLICABLE
    # 61 blocking per run, only 60 listed and no report: a failed tests check cannot be counted
    # once, so the totals and the families are unavailable.
    assert status("blocking_findings", "checkpoint_1") is AssessStatus.UNAVAILABLE
    assert status("blocking_findings.defects", "checkpoint_1") is AssessStatus.UNAVAILABLE
    assert "not all listed" in (values[("blocking_findings.defects", "checkpoint_1")].reason or "")
    assert _value(result, "gate_error", "checkpoint_2") == 1
    assert status("first_pass_clean", "checkpoint_2") is AssessStatus.INSUFFICIENT_EVIDENCE
    assert status("unresolved", "checkpoint_2") is AssessStatus.INSUFFICIENT_EVIDENCE
    assert status("gate_error", "checkpoint_3") is AssessStatus.INSUFFICIENT_EVIDENCE
    assert _value(result, "gate_runs", "checkpoint_3") == 0
    assert status("gate_runs", "checkpoint_4") is AssessStatus.NOT_APPLICABLE
    error_rate = values[("gate_error_rate", TRAJECTORY_SUBJECT)]
    assert (error_rate.numerator, error_rate.denominator) == (1, 2)
    unresolved = values[("unresolved_rate", TRAJECTORY_SUBJECT)]
    assert (unresolved.value, unresolved.denominator) == (1.0, 1)
    assert status("mean_rounds_to_clean", TRAJECTORY_SUBJECT) is AssessStatus.NOT_APPLICABLE
    total = values[("total_first_run_blocking", TRAJECTORY_SUBJECT)]
    assert total.status is AssessStatus.UNAVAILABLE
    assert "checkpoint_2" in (total.reason or "")
    gate = read_step_gate(_trace(exhausted))
    assert gate is not None
    assert run_summary(gate.runs[0]) == (
        "run 1 after stage_1_work: refused, 61 blocking (60 of them listed)"
    )


def test_a_missing_or_broken_record_in_a_gated_trial_is_unavailable(tmp_path: Path) -> None:
    gated, missing, broken = (_step(tmp_path, f"checkpoint_{n}") for n in (1, 2, 3))
    _write_gate(_trace(gated), outputs=[PASSED], termination="gate_clean", signal=True)
    (_trace(broken) / "guardrail-gate").mkdir()
    (_trace(broken) / RECORD_PATH).write_text("{not json")

    ctx = AssessContext(problem="scb/forge", steps=(gated, missing, broken), config={})
    values = _values(gate_effort_scorer().assess(ctx))

    assert values[("gate_runs", "checkpoint_2")].status is AssessStatus.UNAVAILABLE
    assert "other steps have one" in (values[("gate_runs", "checkpoint_2")].reason or "")
    assert values[("gate_runs", "checkpoint_3")].status is AssessStatus.UNAVAILABLE
    assert "not readable JSON" in (values[("gate_runs", "checkpoint_3")].reason or "")
    with pytest.raises(GateRecordError, match="not readable"):
        read_step_gate(_trace(broken))


def test_a_reached_step_without_a_gate_record_still_yields_a_complete_valid_result(
    tmp_path: Path,
) -> None:
    first, missing, last = (_step(tmp_path, f"checkpoint_{n}") for n in (1, 2, 3))
    for step in (first, last):
        _write_gate(_trace(step), outputs=[PASSED], termination="gate_clean", signal=True)

    values = _values(_assess(first, missing, last))

    for metric in ("new_first_run_blocking_raw_net", "new_first_run_blocking_post_autofix_net"):
        assert values[(metric, "checkpoint_2")].status is AssessStatus.UNAVAILABLE
    last_new = values[("new_first_run_blocking_raw_net", "checkpoint_3")]
    assert last_new.status is AssessStatus.UNAVAILABLE
    assert "has no gate record" in (last_new.reason or "")


def test_an_unrecognised_gate_format_leaves_counts_unavailable(tmp_path: Path) -> None:
    step = _step(tmp_path, "checkpoint_1")
    assert step.trace_dir is not None
    _write_gate(
        step.trace_dir,
        outputs=["make: *** [check] Error 1\n", "all good\n"],
        termination="gate_clean",
        signal=True,
        runs=[
            _run_record(1, "stage_1_work", passed=False),
            _run_record(2, "stage_2_fix", passed=True),
        ],
    )

    result = _assess(step)
    values = _values(result)

    assert values[("first_run_blocking", "checkpoint_1")].status is AssessStatus.UNAVAILABLE
    assert "not recognised" in (values[("first_run_blocking", "checkpoint_1")].reason or "")
    assert _value(result, "rounds_to_clean", "checkpoint_1") == 1
    assert "an unknown number of blocking" in result.evidence[0].message


def test_config_is_refused_and_the_scorer_is_registered(tmp_path: Path) -> None:
    step = _step(tmp_path, "checkpoint_1")
    ctx = AssessContext(problem="p", steps=(step,), config={"x": 1})
    assert gate_effort_scorer().assess(ctx).status is AssessStatus.ERROR
    registration = default_scorer_registry().get("gate-effort@10")
    assert registration.load().manifest == GATE_EFFORT


def test_the_wrapper_logs_score_end_to_end(tmp_path: Path, shims: Path) -> None:  # noqa: F811
    agent, environment = _agent(tmp_path, shims, command=GUARDRAIL_STYLE_GATE)
    _run(agent, environment)
    assert len(ScriptedInnerAgent.prompts) == 2
    logs = tmp_path / "logs"
    record = json.loads((logs / RECORD_PATH).read_text())
    first = record["gate_runs"][0]
    assert first["report_dir"] == "guardrail-gate/gate_run_1"
    assert (logs / first["report_dir"] / "gate-1.json").is_file()
    assert all(stage["duration_sec"] is not None for stage in record["stages"])
    step = StepInputs(
        step="checkpoint_1",
        reached=True,
        checkpoint="checkpoint_1",
        snapshot=None,
        snapshot_digest=None,
        record=None,
        record_digest=None,
        trace_dir=logs,
        trace_digest="t",
        agent_result=None,
    )

    result = _assess(step)

    assert _value(result, "rounds_to_clean", "checkpoint_1") == 1
    assert _value(result, "first_run_blocking.complexity", "checkpoint_1") == 1
    assert _value(result, "first_run_blocking.typing", "checkpoint_1") == 1
    assert _value(result, "fix_cost_usd", "checkpoint_1") == pytest.approx(0.01)
    wall = _values(result)[("fix_wall_s", "checkpoint_1")]
    assert wall.status is AssessStatus.OK
    assert (wall.value or 0) >= 0
