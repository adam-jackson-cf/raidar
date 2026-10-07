"""process-compliance@6: "never tested" apart from "tested, then edited" (smoke-3b-3 I3).

In smoke-3b-3 ``work_session_tests_run_rate`` read 0 in both arms: r1's work
sessions never ran a test, r2's ran them at three steps and then made one more
edit. @6 keeps every @5 observation and adds, over the work session only (a gate's
fix sessions never count; a test command counts only if it ran),
``work_session_tests_run_any`` and ``work_session_tests_after_last_code_edit``
(an edit to a test file after the run does not fail it), with their rates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.process_checks_v5 import PROCESS_COMPLIANCE_V5, process_compliance_v5_scorer
from raidar.scorers.process_checks_v6 import PROCESS_COMPLIANCE_V6, process_compliance_v6_scorer
from raidar.scorers.process_checks_v6.work_tests import is_test_file
from raidar.scorers.sdk.context import AssessContext
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessResult, AssessStatus, Observation
from raidar.scorers.sdk.validation import validate_result
from tests.test_process_compliance_v5 import GATE_PATTERN, PROMPT, _ran
from tests.trace_trial_support import (
    AGENT,
    TracedStep,
    agent_step,
    codex_patch,
    delegate,
    session,
    traced_context,
    trajectory,
)

CP1, CP2, CP3 = "checkpoint_1", "checkpoint_2", "checkpoint_3"
ANY, AFTER = "work_session_tests_run_any", "work_session_tests_after_last_code_edit"
V5_WORK = "work_session_tests_run_before_finish"
CODE = codex_patch("e0", "/app/forge.py")


def _ctx(tmp_path: Path, *traces: dict, **config: Any) -> AssessContext:
    steps = [TracedStep(f"checkpoint_{i}", trace=trace) for i, trace in enumerate(traces, 1)]
    return traced_context(tmp_path, steps, extra_test_patterns=[GATE_PATTERN], **config)


def _values(result: AssessResult) -> dict[tuple[str, str], Observation]:
    return {(item.metric, item.subject): item for item in result.observations}


def _v6(ctx: AssessContext) -> dict[tuple[str, str], Observation]:
    return _values(
        validate_result(PROCESS_COMPLIANCE_V6, ctx, process_compliance_v6_scorer().assess(ctx))
    )


def _v5(ctx: AssessContext) -> dict[tuple[str, str], Observation]:
    return _values(
        validate_result(PROCESS_COMPLIANCE_V5, ctx, process_compliance_v5_scorer().assess(ctx))
    )


def _gated(*work: dict[str, Any]) -> dict[str, Any]:
    """A commit gate's step: the work session's ``work`` steps, then a fix session gating."""
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
            session("stage_1_work", PROMPT, *work),
            session("stage_2_fix", *fix),
        ],
    }


def test_never_tested_and_tested_then_edited_differ_only_in_v6(tmp_path: Path) -> None:
    never = _gated(agent_step(2, CODE))
    then_code = _gated(
        agent_step(2, CODE),
        _ran(3, "w2", "python3 -m unittest -q", "Ran 22 tests\n\nOK"),
        agent_step(4, codex_patch("w3", "/app/forge.py", "+y = 2")),
    )
    then_test = _gated(
        agent_step(2, CODE),
        _ran(3, "w2", "pytest -q", "43 passed"),
        agent_step(4, codex_patch("w3", "/app/tests/unit/test_forge.py", "+def test_x(): ...")),
    )

    ctx = _ctx(tmp_path, never, then_code, then_test)
    v5, v6 = _v5(ctx), _v6(ctx)

    assert [v5[(V5_WORK, cp)].value for cp in (CP1, CP2, CP3)] == [0, 0, 0]
    assert [v6[(ANY, cp)].value for cp in (CP1, CP2, CP3)] == [0, 1, 1]
    assert [v6[(AFTER, cp)].value for cp in (CP1, CP2, CP3)] == [0, 0, 1]
    any_rate = v6[(f"{ANY}_rate", TRAJECTORY_SUBJECT)]
    assert (any_rate.numerator, any_rate.denominator) == (2, 3)
    assert v6[(f"{AFTER}_rate", TRAJECTORY_SUBJECT)].value == pytest.approx(1 / 3)


def test_the_gates_runs_and_unrun_commands_never_count(tmp_path: Path) -> None:
    unrun = _gated(
        agent_step(2, CODE),
        _ran(3, "w2", "python -m pytest", "/usr/local/bin/python: No module named pytest"),
    )

    v6 = _v6(_ctx(tmp_path, unrun))

    assert v6[("tests_run_before_finish", CP1)].value == 1, "the gate ran the tests step-wide"
    assert (v6[(ANY, CP1)].value, v6[(AFTER, CP1)].value) == (0, 0)


def test_no_edit_and_test_only_edits_are_not_applicable(tmp_path: Path) -> None:
    no_edit = trajectory(_ran(1, "t1", "pytest -q", "1 passed"))
    tests_only = trajectory(
        agent_step(1, codex_patch("e1", "/app/tests/unit/test_a.py")),
        _ran(2, "t1", "pytest -q", "1 passed"),
    )

    v6 = _v6(_ctx(tmp_path, no_edit, tests_only))

    assert v6[(ANY, CP1)].status is AssessStatus.NOT_APPLICABLE
    assert v6[(AFTER, CP1)].status is AssessStatus.NOT_APPLICABLE
    assert v6[(ANY, CP2)].value == 1
    assert v6[(AFTER, CP2)].status is AssessStatus.NOT_APPLICABLE
    assert v6[(AFTER, CP2)].reason == "the work session edited test files only"
    assert v6[(f"{AFTER}_rate", TRAJECTORY_SUBJECT)].status is AssessStatus.NOT_APPLICABLE


@pytest.mark.parametrize(
    ("relative", "test_file"),
    [
        ("tests/unit/test_forge.py", True),
        ("test_forge.py", True),
        ("pkg/forge_test.py", True),
        ("pkg/test/helpers.py", True),
        ("conftest.py", True),
        ("forge.py", False),
        ("pkg/testing.py", False),
        ("latest/forge.py", False),
    ],
)
def test_test_files_are_the_gates_test_globs(relative: str, test_file: bool) -> None:
    assert is_test_file(relative) is test_file


def test_v6_keeps_every_v5_observation(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        _gated(agent_step(2, CODE)),
        trajectory(agent_step(1, CODE), _ran(2, "t1", "pytest -q", "1 passed")),
    )

    v5, v6 = _v5(ctx), _v6(ctx)

    assert {key for key, value in v5.items() if v6[key] != value} == set()
    added = {(m, s) for m, s in set(v6) - set(v5)}
    assert added == {
        (ANY, CP1),
        (ANY, CP2),
        (AFTER, CP1),
        (AFTER, CP2),
        (f"{ANY}_rate", TRAJECTORY_SUBJECT),
        (f"{AFTER}_rate", TRAJECTORY_SUBJECT),
    }


def test_an_invalid_config_is_an_error_on_every_metric(tmp_path: Path) -> None:
    ctx = traced_context(tmp_path, [TracedStep(CP1, trace=trajectory())], unknown_key=True)

    result = process_compliance_v6_scorer().assess(ctx)

    assert result.status is AssessStatus.ERROR
    assert {item.status for item in result.observations} == {AssessStatus.ERROR}


def test_v6_is_registered_beside_v5() -> None:
    registry = default_scorer_registry()
    v5, v6 = registry.get("process-compliance@5"), registry.get("process-compliance@6")

    assert v6.load().manifest == PROCESS_COMPLIANCE_V6
    assert {m.id for m in v5.manifest.metrics} < {m.id for m in v6.manifest.metrics}
    assert v5.implementation_digest() != v6.implementation_digest()
