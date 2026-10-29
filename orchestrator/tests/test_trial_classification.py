"""Attempt classification (D2) and progression (D3) over trial directories.

Infrastructure failures must never read as agent failures, and the strict
``reached`` vector must be the same for a continue run and a halt run of the
same agent behaviour, with Harbor's own ``min_reward`` gate deciding the halt.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from harbor.trial.multi_step import MultiStepTrial

from raidar.contract.trial import CheckpointState, PlannedTrial, ProgressionRule, TrialHashes
from raidar.design.experiment.schema import ProtocolSpec
from raidar.engine.health.liveness import LIVENESS_REPORT, idle_killed_steps
from raidar.engine.trial.collect import TrialContext, classify_trial, execution_validity
from raidar.engine.trial.progression import derive_reached, gate_passes, progression_rule
from raidar.engine.trial.record import reached
from raidar.grading.solvable import SolvableRef
from raidar.grading.verifier import step_rewards
from tests.execution_support import StepOutcome, gate_rewards, verifier_grade, write_trial

STEPS = tuple(f"checkpoint_{k}" for k in range(1, 9))
HALT = progression_rule(ProtocolSpec(progression="halt_on_failure", min_reward=1.0))
CONTINUE = progression_rule(ProtocolSpec())


def _context(
    task_dir: Path,
    steps: tuple[str, ...] = STEPS,
    rule: ProgressionRule = CONTINUE,
) -> TrialContext:
    hashes = TrialHashes(
        lock="l",
        problem="p",
        arm="a",
        workflow="w",
        packages="s",
        primitive_code="s",
        agent="g",
        protocol="r",
        trial="t",
    )
    trial = PlannedTrial(
        trial_id="b00-p0-baseline",
        attempt=0,
        order=0,
        sequence=0,
        arm="baseline",
        problem="scb/adder@fixture",
        problem_index=0,
        steps=steps,
        hashes=hashes,
    )
    return TrialContext(
        trial=trial,
        planned_steps=tuple((step, step) for step in STEPS),
        task_dir=task_dir,
        provisions=(),
        pinned_harness_version="0.156.1",
        rule=rule,
    )


def _states(step_results: tuple) -> list[str]:
    return [step_result.checkpoint_state.value for step_result in step_results]


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (StepOutcome("checkpoint_1", passed=None, exception="LeakScanError"), "no_verifier_record"),
        (StepOutcome("checkpoint_1", passed=None), "no_verifier_record"),
        (StepOutcome("checkpoint_1", infra="sandbox_error"), "verifier_infra:sandbox_error"),
        (StepOutcome("checkpoint_1", leaked=True), "verifier_leak"),
        (
            StepOutcome("checkpoint_1", passed=False, exception="ApiRateLimitError"),
            "step_exception",
        ),
        (StepOutcome("checkpoint_1", reset_ok=False), "lab_check:context-reset"),
    ],
)
def test_an_infrastructure_failure_is_infra_error_never_failed(
    tmp_path: Path, outcome: StepOutcome, reason: str
) -> None:
    trial_dir = write_trial(tmp_path / "trial", [outcome])
    step_results = classify_trial(trial_dir, _context(tmp_path))
    first = step_results[0]
    assert first.checkpoint_state is CheckpointState.INFRA_ERROR
    assert any(r.startswith(reason) for r in first.infra_reasons), first.infra_reasons
    # A continue run that stops early stopped for infrastructure: the steps it never
    # started are infra errors too, never "not attempted" agent outcomes.
    assert all(a.infra_reasons == ("not_started",) for a in step_results[1:])
    validity = execution_validity(trial_dir, step_results, "0.156.1", CONTINUE)
    assert not validity.passed


def test_agent_outcomes_are_graded_and_keep_the_trial_valid(tmp_path: Path) -> None:
    trial_dir = write_trial(
        tmp_path / "trial",
        [
            StepOutcome("checkpoint_1"),
            StepOutcome("checkpoint_2", passed=False, exception="AgentTimeoutError"),
            StepOutcome("checkpoint_3", passed=False, exception="AgentSafetyRefusalError"),
        ],
    )
    step_results = classify_trial(trial_dir, _context(tmp_path, STEPS[:3]))
    assert _states(step_results)[:4] == ["solved", "failed", "failed", "not_attempted"]
    assert step_results[3].termination == "truncated"
    assert step_results[1].termination == "AgentTimeoutError"
    # A model refusal is the agent's own reward-0 outcome, graded like a timeout.
    assert step_results[2].termination == "AgentSafetyRefusalError"
    assert step_results[0].usage is not None
    assert step_results[0].usage.cost_usd == 0.01
    assert step_results[0].snapshot is not None
    assert step_results[1].parent_snapshot_digest == step_results[0].snapshot.digest
    assert step_results[0].verifier_record == "raidar-oracle/steps/checkpoint_1/record.json"
    assert execution_validity(trial_dir, step_results, "0.156.1", CONTINUE, runnable=3).passed


def test_an_idle_killed_agent_is_graded_like_an_agent_timeout(tmp_path: Path) -> None:
    """Harbor records the kill as a non-zero agent exit; only the liveness record excuses it."""
    exit_error = "NonZeroAgentExitCodeError"
    trial_dir = write_trial(
        tmp_path / "trial",
        [
            StepOutcome("checkpoint_1", passed=False, exception=exit_error),
            StepOutcome("checkpoint_2", passed=False, exception=exit_error),
        ],
    )
    phases = [{"step": "checkpoint_1", "decision": "idle_killed"}]
    (trial_dir / LIVENESS_REPORT).write_text(json.dumps({"phases": phases}))
    step_results = classify_trial(trial_dir, _context(tmp_path, STEPS[:2]))
    assert _states(step_results)[:2] == ["failed", "infra_error"]
    assert step_results[0].termination == exit_error
    assert step_results[1].infra_reasons == (f"step_exception:{exit_error}",)
    assert idle_killed_steps(trial_dir) == {"checkpoint_1"}


def test_trial_level_failures_fail_the_validity_gate(tmp_path: Path) -> None:
    raised = write_trial(tmp_path / "raised", [StepOutcome("checkpoint_1")], trial_exception="X")
    step_results = classify_trial(raised, _context(tmp_path))
    assert step_results[1].infra_reasons == ("not_started:X",)
    assert not execution_validity(raised, step_results, "0.156.1", CONTINUE).passed
    reinstalled = write_trial(tmp_path / "v", [StepOutcome("checkpoint_1")], agent_version="0.1")
    step_results = classify_trial(reinstalled, _context(tmp_path, STEPS[:1]))
    assert execution_validity(reinstalled, step_results, "0.156.1", CONTINUE, runnable=1).passed
    missing = tmp_path / "never-started"
    missing.mkdir()
    for rule in (CONTINUE, HALT):
        step_results = classify_trial(missing, _context(tmp_path, rule=rule))
        # Environment start or step preparation failed: infrastructure, whatever the mode.
        assert _states(step_results) == ["infra_error"] * 8
        assert step_results[0].infra_reasons == ("not_started:no_result",)
        checks = {
            c.name: c for c in execution_validity(missing, step_results, "0.156.1", rule).checks
        }
        assert not checks["steps_started"].passed
        assert reached(rule, step_results, None)[0] == (True,) + (False,) * 7


@pytest.mark.parametrize(
    ("log", "passed", "detail"),
    [
        ("", True, "reports"),
        ("npm install -g @openai/codex@0.156.1; fi && codex --version", True, "reinstalled"),
        ("npm install -g @openai/codex@0.157.0; fi", False, "off-pin ['0.157.0']"),
        ("npm install -g @openai/codex; fi", False, "off-pin ['latest']"),
        ("bootstrap.sh | bash -s -- 2.0.1; fi", False, "off-pin ['2.0.1']"),
    ],
)
def test_the_harness_check_reads_what_harbor_installed(
    tmp_path: Path, log: str, passed: bool, detail: str
) -> None:
    """Harbor reports the configured version, so the install log is the evidence."""
    trial_dir = write_trial(tmp_path / "t", [StepOutcome("checkpoint_1")], agent_version="1")
    if log:
        (trial_dir / "trial.log").write_text(f"Running command: set -euo pipefail; {log}\n")
    step_results = classify_trial(trial_dir, _context(tmp_path, STEPS[:1]))
    checks = execution_validity(trial_dir, step_results, "0.156.1", CONTINUE, runnable=1).checks
    harness = next(c for c in checks if c.name == "agent_version")
    assert harness.passed is passed
    assert detail in harness.detail


def _executed_under_gate(outcomes: list[StepOutcome], rule: ProgressionRule) -> list[StepOutcome]:
    """What Harbor executes in halt mode: its own min_reward gate on the verifier rewards."""
    executed = []
    for outcome in outcomes:
        executed.append(outcome)
        min_reward = rule.harbor_min_reward()
        assert min_reward is not None
        if MultiStepTrial._min_reward_failure(gate_rewards(outcome.name, outcome), min_reward):
            break
    return executed


def _halt_run(passes: list[bool | None], rule: ProgressionRule) -> list[StepOutcome]:
    return _executed_under_gate(
        [
            StepOutcome(
                step,
                passed=passed if passed is not None else False,
                infra="sandbox_error" if passed is None else None,
            )
            for step, passed in zip(STEPS, passes, strict=True)
        ],
        rule,
    )


@pytest.mark.parametrize(
    "passes",
    [
        [True, True, False, True, True, True, True, True],  # checkpoint 3 fails
        [True, None, True, True, True, True, True, True],  # checkpoint 2 has no verdict
        [True] * 8,
    ],
)
def test_derived_strict_reached_equals_the_halt_run(
    tmp_path: Path, passes: list[bool | None]
) -> None:
    continue_run = write_trial(
        tmp_path / "continue",
        [
            StepOutcome(
                s,
                passed=p if p is not None else False,
                infra="sandbox_error" if p is None else None,
            )
            for s, p in zip(STEPS, passes, strict=True)
        ],
    )
    halt_run = write_trial(tmp_path / "halt", _halt_run(passes, HALT))
    continued = classify_trial(continue_run, _context(tmp_path))
    halted = classify_trial(halt_run, _context(tmp_path, rule=HALT))
    derived, _ = reached(CONTINUE, continued, None)
    halt_reached, source = reached(HALT, halted, None)
    assert source == "executed"
    assert derived == halt_reached
    if None not in passes:
        # A valid halt run executed exactly the strict-reached prefix.
        executed = tuple(a.checkpoint_state is not CheckpointState.NOT_ATTEMPTED for a in halted)
        assert executed == derived
        assert execution_validity(halt_run, halted, "0.156.1", HALT).passed
    if passes[2] is False:
        assert derived == (True, True, True, False, False, False, False, False)
    # The halt prefix grades exactly like the continue run's first checkpoints.
    for a, b in zip(halted, continued, strict=True):
        if a.checkpoint_state is not CheckpointState.NOT_ATTEMPTED and a.termination != (
            "not_started"
        ):
            assert a.checkpoint_state == b.checkpoint_state


def test_reached_is_strict_whatever_the_halt_threshold(tmp_path: Path) -> None:
    """min_reward < 1 halts later, but ``reached`` stays SCB-strict and equals the continue run."""
    partial = progression_rule(ProtocolSpec(progression="halt_on_failure", min_reward=0.5))
    # checkpoint_2 passes half its cases (0.5 >= min_reward): the halt run goes on.
    passes: list[bool | None] = [True, False, True, True, True, True, True, True]
    halt_run = write_trial(tmp_path / "halt", _halt_run(passes, partial))
    halted = classify_trial(halt_run, _context(tmp_path, rule=partial))
    assert sum(a.checkpoint_state is not CheckpointState.NOT_ATTEMPTED for a in halted) == 8
    continued = classify_trial(
        write_trial(
            tmp_path / "continue",
            [StepOutcome(s, passed=p) for s, p in zip(STEPS, passes, strict=True)],
        ),
        _context(tmp_path),
    )
    assert reached(partial, halted, None)[0] == reached(CONTINUE, continued, None)[0]
    assert reached(partial, halted, None)[0] == (True, True) + (False,) * 6
    assert execution_validity(halt_run, halted, "0.156.1", partial).passed
    # Harbor running past its own gate (or stopping short of it) is not a valid halt run.
    stopped = write_trial(tmp_path / "short", _halt_run(passes, partial)[:3])
    short = classify_trial(stopped, _context(tmp_path, rule=partial))
    assert not execution_validity(stopped, short, "0.156.1", partial).passed


def test_progression_rule_maps_to_harbor_min_reward() -> None:
    assert CONTINUE.harbor_min_reward() is None
    assert HALT.harbor_min_reward() == {"halt_gate": 1.0}
    partial = progression_rule(ProtocolSpec(progression="halt_on_failure", min_reward=0.5))
    assert partial.harbor_min_reward() == {"halt_gate": 0.5}
    rewards = [
        {"halt_gate": 0.6},
        {"halt_gate": 0.4},
        {"halt_gate": 1.0},
    ]
    # The gate replay honours the rule's own threshold (what Harbor executes) ...
    assert derive_reached(rewards, partial) == (True, True, False)
    strict = [{"strict_pass_rate": 0.6}, {"strict_pass_rate": 0.4}, {"strict_pass_rate": 1.0}]
    assert derive_reached(strict, CONTINUE) == (True, False, False)
    # A truncated run reaches no checkpoint past its cut, in either mode.
    assert derive_reached([{"strict_pass_rate": 1.0}] * 3, CONTINUE, runnable=2) == (
        True,
        True,
        False,
    )


def test_halt_gates_on_the_solvable_verdict_not_the_raw_grade(tmp_path: Path) -> None:
    """A case the reference itself fails cannot stop a trial (a raw failure the set forgives)."""
    steps = [
        StepOutcome(step, passed=step != "checkpoint_2", forgiven=step == "checkpoint_2")
        for step in STEPS
    ]
    # Raw grade fails checkpoint_2, but its solvable set (case b only) passes: the gate holds.
    assert gate_rewards("checkpoint_2", steps[1])["strict_pass_rate"] == 0.5
    assert gate_rewards("checkpoint_2", steps[1])["solvable_pass_rate"] == 1.0
    halt_run = write_trial(tmp_path / "halt", steps)
    halted = classify_trial(halt_run, _context(tmp_path, rule=HALT))
    assert all(a.checkpoint_state is not CheckpointState.NOT_ATTEMPTED for a in halted)
    assert halted[1].checkpoint_state is CheckpointState.FAILED
    assert execution_validity(halt_run, halted, "0.156.1", HALT).passed
    # Strict ``reached`` still stops at the raw failure.
    assert reached(HALT, halted, None)[0] == (True, True) + (False,) * 6


def test_halt_stops_where_the_solvable_set_fails(tmp_path: Path) -> None:
    outcomes = _halt_run([True, False, True, True, True, True, True, True], HALT)
    assert [o.name for o in outcomes] == ["checkpoint_1", "checkpoint_2"]


# Lock-pinned excluded steps (empty solvable set): metric_transform_lang checkpoint_1 and
# mocked_http checkpoint_7 are pinned ``no_new_scoreable_tests``.
@pytest.mark.parametrize("at", [0, 6], ids=["mtl-checkpoint_1", "mocked_http-checkpoint_7"])
@pytest.mark.parametrize("raw_passed", [True, False], ids=["raw-pass", "raw-fail"])
def test_a_pinned_excluded_step_runs_without_gating_and_is_reported_excluded(
    tmp_path: Path, at: int, raw_passed: bool
) -> None:
    outcomes = [
        StepOutcome(step, passed=raw_passed if k == at else True, excluded=k == at)
        for k, step in enumerate(STEPS)
    ]
    rewards = gate_rewards(STEPS[at], outcomes[at])
    # A separate gate signal, never a solvable verdict analysis could read as solved.
    assert "solvable_pass_rate" not in rewards
    assert rewards["solvable_excluded"] == 1.0
    assert HALT.harbor_min_reward() == {"halt_gate": 1.0}
    assert _executed_under_gate(outcomes, HALT) == outcomes  # Harbor's gate lets the run on

    halt_run = write_trial(tmp_path / "halt", outcomes, seeded=True)
    halted = classify_trial(halt_run, _context(tmp_path, rule=HALT))
    expected = ["solved"] * 8
    expected[at] = "excluded"
    assert _states(halted) == expected  # neither solved nor failed, and every step ran
    assert execution_validity(halt_run, halted, "0.156.1", HALT).passed
    # The agent's workspace carries on from the excluded step.
    snapshot = halted[at].snapshot
    assert snapshot is not None
    if at < 7:
        assert halted[at + 1].parent_snapshot_digest == snapshot.digest
    # Raw strict ``reached`` keeps the excluded step's raw verdict.
    strict, source = reached(HALT, halted, None)
    assert source == "executed"
    assert strict == tuple(raw_passed or k <= at for k in range(8))

    continued = classify_trial(write_trial(tmp_path / "cont", outcomes), _context(tmp_path))
    assert _states(continued) == expected  # continue mode reports the same exclusion
    assert reached(CONTINUE, continued, None)[0] == strict


@pytest.mark.parametrize("failing", ["failed", "infra", "unpinned", "leaked"])
def test_halt_still_stops_where_there_is_no_pinned_exclusion(tmp_path: Path, failing: str) -> None:
    """Fail closed: only an explicit empty pin clears the gate; everything else halts."""
    stop = {
        "failed": StepOutcome(STEPS[1], passed=False),
        "infra": StepOutcome(STEPS[1], infra="sandbox_error", excluded=True),
        "unpinned": StepOutcome(STEPS[1]),
        "leaked": StepOutcome(STEPS[1], leaked=True, excluded=True),
    }[failing]
    if failing == "unpinned":
        grade = verifier_grade(STEPS[1], stop)
        rewards = SolvableRef(sha256="s", steps={}).gate_rewards(STEPS[1], grade)
        assert "halt_gate" not in rewards  # missing validation is not an exclusion
        assert MultiStepTrial._min_reward_failure(rewards, {"halt_gate": 1.0}) is not None
        assert not gate_passes(rewards, HALT)
        return
    if failing == "leaked":
        leaked = step_rewards(gate_rewards(STEPS[1], stop), leaked=True)
        assert MultiStepTrial._min_reward_failure(leaked, {"halt_gate": 1.0}) is not None
        return
    outcomes = [StepOutcome(STEPS[0], excluded=True), stop, *(StepOutcome(s) for s in STEPS[2:])]
    executed = _executed_under_gate(outcomes, HALT)
    assert [o.name for o in executed] == list(STEPS[:2])
    halt_run = write_trial(tmp_path / "halt", executed)
    halted = classify_trial(halt_run, _context(tmp_path, rule=HALT))
    assert halted[0].checkpoint_state is CheckpointState.EXCLUDED
    assert halted[1].checkpoint_state is (
        CheckpointState.FAILED if failing == "failed" else CheckpointState.INFRA_ERROR
    )
    expected = CheckpointState.NOT_ATTEMPTED if failing == "failed" else CheckpointState.INFRA_ERROR
    assert all(a.checkpoint_state is expected for a in halted[2:])
