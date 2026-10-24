"""process-compliance: deterministic process checks (Jev parked 2026-10-04, F7).

A check the trace decides is ``ok``; a check it cannot decide is undetermined
(``insufficient_evidence`` with a reason, never a value, never a zero); nothing
asks, binds or audits Jev.

The work session's test runs are read two more ways than ``work_session_tests_run_before_finish``
(smoke-3b-3 I3): in smoke-3b-3 ``work_session_tests_run_rate`` read 0 in both arms,
because r1's work sessions never ran a test and r2's ran them at three steps and
then made one more edit. ``work_session_tests_run_any`` and
``work_session_tests_after_last_code_edit`` (an edit to a test file after the run
does not fail it) tell those apart, with their rates, over the work session only
(a gate's fix sessions never count; a test command counts only if it ran).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.scoring.framework import catalog
from raidar.scoring.framework.catalog import default_scorer_registry
from raidar.scoring.framework.context import AssessContext
from raidar.scoring.framework.manifest import Tier
from raidar.scoring.framework.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
    Provenance,
)
from raidar.scoring.framework.store import AssessmentStore
from raidar.scoring.framework.validation import validate_result
from raidar.scoring.process_checks import (
    DeterministicProcessComplianceScorer,
    deterministic_process_compliance_scorer,
)
from raidar.scoring.process_checks.manifest import PROCESS_COMPLIANCE
from raidar.scoring.process_checks.work_tests import is_test_file
from tests.test_process_compliance_work_session import GATE_PATTERN, PROMPT, _ran
from tests.trace_trial_support import (
    AGENT,
    TracedStep,
    agent_step,
    codex_exec,
    codex_patch,
    delegate,
    session,
    tool_call,
    traced_context,
    trajectory,
    write_traced_trial,
)

REF = "process-compliance@6"
CHECKS = ("tests_run_before_finish", "plan_written")
PLAN = tool_call("p0", "update_plan", {"plan": [{"step": "write"}]})
EDIT = codex_patch("e0", "/app/app/main.py")
UNDETERMINED = AssessStatus.INSUFFICIENT_EVIDENCE
CP1, CP2, CP3 = "checkpoint_1", "checkpoint_2", "checkpoint_3"
ANY, AFTER = "work_session_tests_run_any", "work_session_tests_after_last_code_edit"
WORK = "work_session_tests_run_before_finish"
CODE = codex_patch("e0", "/app/forge.py")


def _ambiguous() -> dict:
    """A message-only plan and an unparsed in-place write after the test run."""
    return trajectory(
        agent_step(1, message="Plan:\n1. edit main\n2. run tests"),
        agent_step(2, codex_patch("e1", "/app/app/main.py")),
        agent_step(3, codex_exec("t1", "pytest -q")),
        agent_step(4, codex_exec("s1", "sed -i 's/a/b/' app/main.py")),
    )


def _decided_pass() -> dict:
    return trajectory(
        agent_step(1, PLAN), agent_step(2, EDIT), agent_step(3, codex_exec("t1", "pytest"))
    )


def _decided_fail() -> dict:
    return trajectory(agent_step(1, codex_exec("t1", "pytest")), agent_step(2, EDIT))


def _no_edit() -> dict:
    return trajectory(agent_step(1, codex_exec("r1", "cat app/main.py")))


def _plain_ctx(tmp_path: Path, *traces: dict, **config: Any) -> AssessContext:
    steps = [TracedStep(f"checkpoint_{i}", trace=trace) for i, trace in enumerate(traces, 1)]
    return traced_context(tmp_path, steps, **config)


def _assess(ctx: AssessContext) -> AssessResult:
    scorer = DeterministicProcessComplianceScorer()
    return validate_result(scorer.manifest, ctx, scorer.assess(ctx))


def _by_key(result: AssessResult) -> dict[tuple[str, str], Observation]:
    return {(item.metric, item.subject): item for item in result.observations}


def test_the_manifest_has_only_deterministic_metrics_and_no_jev_binding() -> None:
    assert PROCESS_COMPLIANCE.ref == REF
    ids = [metric.id for metric in PROCESS_COMPLIANCE.metrics]
    assert {*CHECKS, "compliance_rate", WORK, ANY, AFTER} <= set(ids)
    assert {metric.tier for metric in PROCESS_COMPLIANCE.metrics} == {Tier.DETERMINISTIC}
    registration = default_scorer_registry().get(REF)
    assert registration.store_implementation is None
    assert registration.load().manifest == PROCESS_COMPLIANCE


def test_its_implementation_digest_covers_no_jev_code(monkeypatch: pytest.MonkeyPatch) -> None:
    modules: list[str] = []
    original = catalog.digest_json

    def spy(value: Any) -> str:
        if isinstance(value, dict) and "modules" in value:
            modules.extend(value["modules"])
        return original(value)

    monkeypatch.setattr(catalog, "digest_json", spy)
    default_scorer_registry().get(REF).implementation_digest()
    assert "raidar.scoring.process_checks.checks" in modules
    assert not [
        m
        for m in modules
        if m.startswith(("raidar.scoring.judge", "raidar.scoring.framework.process_compliance"))
    ]


def test_undecidable_checks_are_undetermined_with_a_reason_never_zero(tmp_path: Path) -> None:
    result = _assess(
        _plain_ctx(tmp_path, _ambiguous(), _decided_pass(), plan_in_compliance_rate=True)
    )
    observed = _by_key(result)
    for check in CHECKS:
        item = observed[check, "checkpoint_1"]
        assert (item.status, item.value) == (UNDETERMINED, None)
        assert (item.reason or "").startswith("undetermined: ")
    # Only the decided step counts: two passes out of two decided checks.
    rate = observed["compliance_rate", "trajectory"]
    assert (rate.status, rate.value, rate.numerator, rate.denominator) == (
        AssessStatus.OK,
        1.0,
        2,
        2,
    )
    assert not [item for item in result.observations if item.metric.startswith("jev")]
    assert result.provenance == Provenance()


def test_a_trajectory_with_only_undetermined_checks_has_no_rate_not_a_zero(
    tmp_path: Path,
) -> None:
    result = _assess(_plain_ctx(tmp_path, _ambiguous()))
    rate = _by_key(result)["compliance_rate", "trajectory"]
    assert (rate.status, rate.value) == (AssessStatus.NOT_APPLICABLE, None)


def test_decided_checks_are_ok_and_undetermined_ones_are_not(tmp_path: Path) -> None:
    ctx = _plain_ctx(tmp_path, _decided_pass(), _decided_fail(), _no_edit(), _ambiguous())
    current = _by_key(_assess(ctx))
    assert current["tests_run_before_finish", "checkpoint_1"].value == 1.0
    assert current["tests_run_before_finish", "checkpoint_2"].value == 0.0
    assert current["plan_written", "checkpoint_3"].status is AssessStatus.NOT_APPLICABLE
    assert current["plan_written", "checkpoint_4"].status is UNDETERMINED


def test_invalid_config_is_an_error(tmp_path: Path) -> None:
    result = _assess(_plain_ctx(tmp_path, _decided_pass(), extra_test_patterns=["("]))
    assert result.status is AssessStatus.ERROR


def test_an_invalid_config_is_an_error_on_every_metric(tmp_path: Path) -> None:
    ctx = traced_context(tmp_path, [TracedStep(CP1, trace=trajectory())], unknown_key=True)

    result = deterministic_process_compliance_scorer().assess(ctx)

    assert result.status is AssessStatus.ERROR
    assert {item.status for item in result.observations} == {AssessStatus.ERROR}


def test_rescore_writes_no_jev_audit_and_nothing_is_provisional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No key: nothing to answer, so nothing awaits.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    trial = write_traced_trial(
        tmp_path / "jobs" / "trial", [TracedStep("checkpoint_1", trace=_ambiguous())]
    )
    store = tmp_path / "store"
    args = ["assess", "--rescore", str(trial), "--scorer", REF, "--store", str(store)]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    (entry,) = json.loads(result.output)["assessments"]
    stored = AssessmentStore(store).get(entry["key"])
    assert stored is not None
    assert stored.provisional is False
    assert not (store / "jev-audit").exists()
    item = _by_key(stored.result)["plan_written", "checkpoint_1"]
    assert (item.status, item.value) == (UNDETERMINED, None)

    replay = CliRunner().invoke(main, [*args, "--replay"])
    assert replay.exit_code == 0, replay.output
    assert json.loads(replay.output)["counts"]["replayed_identical"] == 1


# ---- the work session's test runs -----------------------------------------------------


def _ctx(tmp_path: Path, *traces: dict, **config: Any) -> AssessContext:
    steps = [TracedStep(f"checkpoint_{i}", trace=trace) for i, trace in enumerate(traces, 1)]
    return traced_context(tmp_path, steps, extra_test_patterns=[GATE_PATTERN], **config)


def _values(ctx: AssessContext) -> dict[tuple[str, str], Observation]:
    return _by_key(_assess(ctx))


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


def test_never_tested_and_tested_then_edited_differ_in_the_work_session_checks(
    tmp_path: Path,
) -> None:
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

    values = _values(_ctx(tmp_path, never, then_code, then_test))

    assert [values[(WORK, cp)].value for cp in (CP1, CP2, CP3)] == [0, 0, 0]
    assert [values[(ANY, cp)].value for cp in (CP1, CP2, CP3)] == [0, 1, 1]
    assert [values[(AFTER, cp)].value for cp in (CP1, CP2, CP3)] == [0, 0, 1]
    any_rate = values[(f"{ANY}_rate", TRAJECTORY_SUBJECT)]
    assert (any_rate.numerator, any_rate.denominator) == (2, 3)
    assert values[(f"{AFTER}_rate", TRAJECTORY_SUBJECT)].value == pytest.approx(1 / 3)


def test_the_gates_runs_and_unrun_commands_never_count(tmp_path: Path) -> None:
    unrun = _gated(
        agent_step(2, CODE),
        _ran(3, "w2", "python -m pytest", "/usr/local/bin/python: No module named pytest"),
    )

    values = _values(_ctx(tmp_path, unrun))

    assert values[("tests_run_before_finish", CP1)].value == 1, "the gate ran the tests step-wide"
    assert (values[(ANY, CP1)].value, values[(AFTER, CP1)].value) == (0, 0)


def test_no_edit_and_test_only_edits_are_not_applicable(tmp_path: Path) -> None:
    no_edit = trajectory(_ran(1, "t1", "pytest -q", "1 passed"))
    tests_only = trajectory(
        agent_step(1, codex_patch("e1", "/app/tests/unit/test_a.py")),
        _ran(2, "t1", "pytest -q", "1 passed"),
    )

    values = _values(_ctx(tmp_path, no_edit, tests_only))

    assert values[(ANY, CP1)].status is AssessStatus.NOT_APPLICABLE
    assert values[(AFTER, CP1)].status is AssessStatus.NOT_APPLICABLE
    assert values[(ANY, CP2)].value == 1
    assert values[(AFTER, CP2)].status is AssessStatus.NOT_APPLICABLE
    assert values[(AFTER, CP2)].reason == "the work session edited test files only"
    assert values[(f"{AFTER}_rate", TRAJECTORY_SUBJECT)].status is AssessStatus.NOT_APPLICABLE


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


def test_the_observations_cover_every_check_at_every_step(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        _gated(agent_step(2, CODE)),
        trajectory(agent_step(1, CODE), _ran(2, "t1", "pytest -q", "1 passed")),
    )

    values = _values(ctx)

    for checkpoint in (CP1, CP2):
        for metric in (*CHECKS, "test_commands_not_run", WORK, ANY, AFTER):
            assert (metric, checkpoint) in values
    for metric in ("total_test_commands_not_run", f"{ANY}_rate", f"{AFTER}_rate", "stall_rate"):
        assert (metric, TRAJECTORY_SUBJECT) in values
