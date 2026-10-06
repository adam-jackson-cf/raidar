"""process-compliance@4: a test command counts as a test run only when it ran (smoke-04 O2).

In smoke-04 a ``python -m pytest -q`` that failed with "No module named pytest"
counted as a test run, and the pack's gate (which runs the project's tests) did
not count. @4 drops a test command whose recorded output says its runner was not
found (``test_commands_not_run``), keeps @3's reading when nothing was recorded,
and lets the experiment name its gate command through ``extra_test_patterns``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.process_checks_v3 import PROCESS_COMPLIANCE_V3, process_compliance_v3_scorer
from raidar.scorers.process_checks_v4 import PROCESS_COMPLIANCE_V4, process_compliance_v4_scorer
from raidar.scorers.process_checks_v4.scorer import not_found, runner_of
from raidar.scorers.sdk.context import AssessContext
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessResult, AssessStatus, Observation
from raidar.scorers.sdk.validation import validate_result
from tests.trace_trial_support import (
    TracedStep,
    agent_step,
    codex_exec,
    codex_patch,
    traced_context,
    trajectory,
)

CP1, CP2 = "checkpoint_1", "checkpoint_2"
CHECK = "tests_run_before_finish"
EDIT = codex_patch("e0", "/app/app/main.py")
# The guardrails experiment's pattern for its gate command (experiment.yaml).
GATE_PATTERN = r"guardrail-check(?![\w.-])(?!\s+--(?:version|help)\b)"


def _ran(step_id: int, call_id: str, cmd: str, output: str | None) -> dict[str, Any]:
    step = agent_step(step_id, codex_exec(call_id, cmd))
    if output is not None:
        step["observation"] = {"results": [{"source_call_id": call_id, "content": output}]}
    return step


def _ctx(tmp_path: Path, *traces: dict, **config: Any) -> AssessContext:
    steps = [TracedStep(f"checkpoint_{i}", trace=trace) for i, trace in enumerate(traces, 1)]
    return traced_context(tmp_path, steps, **config)


def _values(result: AssessResult) -> dict[tuple[str, str], Observation]:
    return {(item.metric, item.subject): item for item in result.observations}


def _v4(ctx: AssessContext) -> dict[tuple[str, str], Observation]:
    return _values(
        validate_result(PROCESS_COMPLIANCE_V4, ctx, process_compliance_v4_scorer().assess(ctx))
    )


def _v3(ctx: AssessContext) -> dict[tuple[str, str], Observation]:
    return _values(
        validate_result(PROCESS_COMPLIANCE_V3, ctx, process_compliance_v3_scorer().assess(ctx))
    )


@pytest.mark.parametrize(
    ("cmd", "output"),
    [
        ("python -m pytest -q", "/usr/bin/python3: No module named pytest"),
        ("pytest -q", "bash: line 1: pytest: command not found"),
        ("uv run pytest", "error: Failed to spawn: `pytest`"),
    ],
)
def test_a_test_command_whose_runner_was_not_found_is_not_a_test_run(
    tmp_path: Path, cmd: str, output: str
) -> None:
    ctx = _ctx(tmp_path, trajectory(agent_step(1, EDIT), _ran(2, "t1", cmd, output)))

    v3, v4 = _v3(ctx), _v4(ctx)

    assert v3[(CHECK, CP1)].value == 1, "@3 counted the command text"
    assert v4[(CHECK, CP1)].value == 0
    assert v4[("test_commands_not_run", CP1)].value == 1
    assert v4[("total_test_commands_not_run", TRAJECTORY_SUBJECT)].value == 1


def test_a_test_command_that_ran_still_counts(tmp_path: Path) -> None:
    output = "3 passed in 0.12s"
    ctx = _ctx(tmp_path, trajectory(agent_step(1, EDIT), _ran(2, "t1", "pytest -q", output)))

    v4 = _v4(ctx)

    assert v4[(CHECK, CP1)].value == 1
    assert v4[("test_commands_not_run", CP1)].value == 0


def test_a_command_without_recorded_output_keeps_v3s_reading(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, trajectory(agent_step(1, EDIT), _ran(2, "t1", "pytest -q", None)))

    v3, v4 = _v3(ctx), _v4(ctx)

    assert all(v4[key] == value for key, value in v3.items())
    assert v4[("test_commands_not_run", CP1)].value == 0


def test_the_experiments_gate_pattern_counts_the_gate_as_a_test_run(tmp_path: Path) -> None:
    gated = trajectory(
        agent_step(1, EDIT),
        _ran(2, "g1", "guardrail-check --fix && guardrail-check", "guardrail-check: PASSED"),
    )
    asked = trajectory(
        agent_step(1, EDIT), _ran(2, "g1", "guardrail-check --version", "python-guardrails@5")
    )

    counted = _v4(_ctx(tmp_path / "a", gated, extra_test_patterns=[GATE_PATTERN]))
    version = _v4(_ctx(tmp_path / "b", asked, extra_test_patterns=[GATE_PATTERN]))
    plain = _v4(_ctx(tmp_path / "c", gated))

    assert counted[(CHECK, CP1)].value == 1
    assert version[(CHECK, CP1)].value == 0
    assert plain[(CHECK, CP1)].value == 0


def test_v4_keeps_every_v3_observation_when_every_test_command_ran(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        trajectory(agent_step(1, EDIT), _ran(2, "t1", "pytest", "1 passed")),
        trajectory(agent_step(1, EDIT)),
    )

    v3, v4 = _v3(ctx), _v4(ctx)

    assert all(v4[key] == value for key, value in v3.items())
    assert set(v4) - set(v3) == {
        ("test_commands_not_run", CP1),
        ("test_commands_not_run", CP2),
        ("total_test_commands_not_run", TRAJECTORY_SUBJECT),
    }
    assert v4[("total_test_commands_not_run", TRAJECTORY_SUBJECT)].status is AssessStatus.OK


@pytest.mark.parametrize(
    ("command", "runner"),
    [
        ("pytest -q", ("pytest", None)),
        ("uv run pytest tests", ("pytest", None)),
        ("timeout 60 python3 -m pytest -q", ("python3", "pytest")),
        ("FOO=1 python -B -m unittest discover", ("python", "unittest")),
    ],
)
def test_the_runner_is_the_first_word_after_launchers_or_the_python_module(
    command: str, runner: tuple[str, str | None]
) -> None:
    assert runner_of(command) == runner


def test_not_found_matches_only_the_commands_own_runner() -> None:
    assert not_found("python -m pytest", "No module named 'pytest'")
    assert not not_found("python -m pytest", "E   ModuleNotFoundError: No module named 'forge'")
    assert not not_found("pytest", "my-pytest: command not found")
    assert not_found("pytest", "/bin/sh: 1: pytest: not found")


def test_v4_is_registered_beside_v3() -> None:
    registry = default_scorer_registry()
    v3, v4 = registry.get("process-compliance@3"), registry.get("process-compliance@4")

    assert v4.load().manifest == PROCESS_COMPLIANCE_V4
    assert {m.id for m in v3.manifest.metrics} < {m.id for m in v4.manifest.metrics}
    assert v3.implementation_digest() != v4.implementation_digest()
