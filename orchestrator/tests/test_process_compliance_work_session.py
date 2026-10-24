"""process-compliance: the agent's own process, not the gate around it (smoke-3b-2 O4).

In smoke-3b-2 ``compliance_rate`` was r1 0 and r2 0.5: ``plan_written`` failed at
every step of both arms (neither condition asks for a plan), and r2's
``tests_run_before_finish`` passed only through the gate's fix sessions running
``guardrail-check``. The work session's own test-run check and rate are reported
apart, and ``plan_written`` counts in the rate only when the assessment says the
condition asks for a plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from raidar.scoring.framework.context import AssessContext
from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, AssessStatus, Observation
from raidar.scoring.framework.validation import validate_result
from raidar.scoring.process_checks import deterministic_process_compliance_scorer
from raidar.scoring.process_checks.manifest import PROCESS_COMPLIANCE
from tests.trace_trial_support import (
    AGENT,
    TracedStep,
    agent_step,
    codex_exec,
    codex_patch,
    delegate,
    session,
    traced_context,
    trajectory,
)

CP1, CP2 = "checkpoint_1", "checkpoint_2"
CHECK = "tests_run_before_finish"
WORK = "work_session_tests_run_before_finish"
RATE = "work_session_tests_run_rate"
EDIT = codex_patch("e0", "/app/app/main.py")
GATE_PATTERN = r"guardrail-check(?![\w.-])(?!\s+--(?:version|help)\b)"
PROMPT = {"source": "user", "message": "Complete the checkpoint."}


def _ran(step_id: int, call_id: str, cmd: str, output: str | None) -> dict[str, Any]:
    step = agent_step(step_id, codex_exec(call_id, cmd))
    if output is not None:
        step["observation"] = {"results": [{"source_call_id": call_id, "content": output}]}
    return step


def _gated(work_tests: bool) -> dict[str, Any]:
    """A commit gate's step: the work session edits (and maybe tests), a fix session gates."""
    work = [PROMPT, agent_step(2, codex_patch("w1", "/app/forge.py"))]
    if work_tests:
        work.append(_ran(3, "w2", "python3 -m unittest -q", "Ran 4 tests\n\nOK"))
    fix = [
        PROMPT,
        agent_step(2, codex_patch("f1", "/app/forge.py")),
        _ran(3, "f2", "guardrail-check", "guardrail-check: PASSED"),
    ]
    return {
        "schema_version": "ATIF-v1.7",
        "session_id": "main",
        "agent": dict(AGENT),
        "steps": [
            {"step_id": 1, "message": "stage_1_work", **delegate("stage_1_work")},
            {"step_id": 2, "message": "stage_2_fix", **delegate("stage_2_fix")},
        ],
        "subagent_trajectories": [
            session("stage_1_work", *work),
            session("stage_2_fix", *fix),
        ],
    }


def _ctx(tmp_path: Path, *traces: dict, **config: Any) -> AssessContext:
    steps = [TracedStep(f"checkpoint_{i}", trace=trace) for i, trace in enumerate(traces, 1)]
    return traced_context(tmp_path, steps, extra_test_patterns=[GATE_PATTERN], **config)


def _values(ctx: AssessContext) -> dict[tuple[str, str], Observation]:
    scorer = deterministic_process_compliance_scorer()
    result = validate_result(PROCESS_COMPLIANCE, ctx, scorer.assess(ctx))
    return {(item.metric, item.subject): item for item in result.observations}


def test_the_gates_own_test_run_does_not_count_for_the_work_session(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _gated(work_tests=False), _gated(work_tests=True))

    values = _values(ctx)

    assert (values[(CHECK, CP1)].value, values[(CHECK, CP2)].value) == (1, 1), "the gate ran tests"
    assert (values[(WORK, CP1)].value, values[(WORK, CP2)].value) == (0, 1)
    rate = values[(RATE, TRAJECTORY_SUBJECT)]
    assert (rate.value, rate.numerator, rate.denominator) == (0.5, 1, 2)


def test_without_wrapper_sessions_the_work_check_is_the_step_wide_check(tmp_path: Path) -> None:
    tested = trajectory(agent_step(1, EDIT), _ran(2, "t1", "pytest -q", "1 passed"))
    untested = trajectory(agent_step(1, EDIT))

    values = _values(_ctx(tmp_path, tested, untested))

    for step in (CP1, CP2):
        assert values[(WORK, step)].value == values[(CHECK, step)].value
    assert values[(RATE, TRAJECTORY_SUBJECT)].value == 0.5


def test_a_work_session_without_an_edit_is_not_applicable(tmp_path: Path) -> None:
    values = _values(_ctx(tmp_path, trajectory(_ran(1, "t1", "pytest -q", "1 passed"))))

    assert values[(WORK, CP1)].status is AssessStatus.NOT_APPLICABLE
    assert values[(RATE, TRAJECTORY_SUBJECT)].status is AssessStatus.NOT_APPLICABLE


def test_the_rate_leaves_plan_written_out_unless_the_condition_asks_for_a_plan(
    tmp_path: Path,
) -> None:
    tested = trajectory(agent_step(1, EDIT), _ran(2, "t1", "pytest -q", "1 passed"))

    default = _values(_ctx(tmp_path / "a", tested))
    planned = _values(_ctx(tmp_path / "b", tested, plan_in_compliance_rate=True))

    assert default[("plan_written", CP1)].value == 0, "still reported"
    assert default[("compliance_rate", TRAJECTORY_SUBJECT)].value == 1.0
    assert planned[("compliance_rate", TRAJECTORY_SUBJECT)].value == 0.5


def test_a_test_command_that_never_ran_does_not_count_for_the_work_session(
    tmp_path: Path,
) -> None:
    ctx = _ctx(
        tmp_path,
        _gated(work_tests=False),
        trajectory(
            agent_step(1, EDIT), _ran(2, "t1", "python -m pytest", "No module named pytest")
        ),
    )

    values = _values(ctx)

    assert values[("total_test_commands_not_run", TRAJECTORY_SUBJECT)].value == 1
    assert values[(WORK, CP2)].value == 0
