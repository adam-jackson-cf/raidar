"""Attempt classification (D2) and progression (D3) decided from in-memory trial evidence.

Infrastructure failures must never read as agent failures, and the strict
``reached`` vector must be the same for a continue run and a halt run of the
same agent behaviour, with Harbor's own ``min_reward`` gate deciding the halt.
The file-reading seam is proven in ``test_trial_execution_slow``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from harbor.trial.multi_step import MultiStepTrial

from raidar.contract.trial import CheckpointState, ProgressionRule, StepResult
from raidar.design.experiment.schema import ProtocolSpec
from raidar.engine.trial.collect import (
    TrialEvidence,
    agent_version_check,
    classify_evidence,
    classify_trial,
    execution_validity,
    harness_installs,
    installed_versions,
    read_evidence,
    trial_exception,
    validity_from_evidence,
)
from raidar.engine.trial.progression import derive_reached, gate_passes, progression_rule
from raidar.engine.trial.record import reached
from raidar.grading.solvable import SolvableRef
from raidar.grading.verifier import step_rewards
from tests.execution_support import (
    CLASSIFIED_STEPS as STEPS,
)
from tests.execution_support import (
    StepOutcome,
    classification_context,
    gate_rewards,
    harbor_result,
    trial_evidence,
    verifier_grade,
)

HALT = progression_rule(ProtocolSpec(progression="halt_on_failure", min_reward=1.0))
CONTINUE = progression_rule(ProtocolSpec())


def _classify(
    outcomes: list[StepOutcome],
    rule: ProgressionRule = CONTINUE,
    steps: tuple[str, ...] = STEPS,
    *,
    trial_exception: str | None = None,
    agent_version: str = "0.156.1",
    result: bool = True,
    idle_killed: tuple[str, ...] = (),
) -> tuple[TrialEvidence, tuple[StepResult, ...]]:
    gathered = trial_evidence(
        outcomes,
        trial_exception=trial_exception,
        agent_version=agent_version,
        result=result,
        idle_killed=idle_killed,
    )
    return gathered, classify_evidence(classification_context(steps, rule), gathered)


def _valid(
    gathered: TrialEvidence,
    step_results: tuple[StepResult, ...],
    rule: ProgressionRule,
    *,
    runnable: int | None = None,
    installs: tuple[str, ...] = (),
) -> bool:
    return validity_from_evidence(
        gathered.result, installs, step_results, "0.156.1", rule, runnable=runnable
    ).passed


def _states(step_results: tuple[StepResult, ...]) -> list[str]:
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
    outcome: StepOutcome, reason: str
) -> None:
    gathered, step_results = _classify([outcome])
    first = step_results[0]
    assert first.checkpoint_state is CheckpointState.INFRA_ERROR
    assert any(r.startswith(reason) for r in first.infra_reasons), first.infra_reasons
    # A continue run that stops early stopped for infrastructure: the steps it never
    # started are infra errors too, never "not attempted" agent outcomes.
    assert all(a.infra_reasons == ("not_started",) for a in step_results[1:])
    assert not _valid(gathered, step_results, CONTINUE)


def test_an_agent_reap_failure_is_an_infrastructure_failure() -> None:
    gathered = trial_evidence([StepOutcome("checkpoint_1")])
    reaped = replace(gathered.steps["checkpoint_1"], reap="agent_reap_failed:1")
    evidence = TrialEvidence(gathered.result, {"checkpoint_1": reaped}, None)
    first = classify_evidence(classification_context(), evidence)[0]
    assert first.checkpoint_state is CheckpointState.INFRA_ERROR
    assert first.infra_reasons == ("agent_reap_failed:1",)


def test_agent_outcomes_are_graded_and_keep_the_trial_valid() -> None:
    gathered, step_results = _classify(
        [
            StepOutcome("checkpoint_1"),
            StepOutcome("checkpoint_2", passed=False, exception="AgentTimeoutError"),
            StepOutcome("checkpoint_3", passed=False, exception="AgentSafetyRefusalError"),
        ],
        steps=STEPS[:3],
    )
    assert _states(step_results)[:4] == ["solved", "failed", "failed", "not_attempted"]
    assert step_results[3].termination == "truncated"
    assert step_results[1].termination == "AgentTimeoutError"
    # A model refusal is the agent's own reward-0 outcome, graded like a timeout.
    assert step_results[2].termination == "AgentSafetyRefusalError"
    assert step_results[0].usage is not None
    assert step_results[0].usage.cost_usd == 0.01
    assert step_results[0].snapshot is not None
    assert step_results[1].parent_snapshot_digest == step_results[0].snapshot.digest
    assert _valid(gathered, step_results, CONTINUE, runnable=3)


def test_an_idle_killed_agent_is_graded_like_an_agent_timeout() -> None:
    """Harbor records the kill as a non-zero agent exit; only the liveness record excuses it."""
    exit_error = "NonZeroAgentExitCodeError"
    _, step_results = _classify(
        [
            StepOutcome("checkpoint_1", passed=False, exception=exit_error),
            StepOutcome("checkpoint_2", passed=False, exception=exit_error),
        ],
        steps=STEPS[:2],
        idle_killed=("checkpoint_1",),
    )
    assert _states(step_results)[:2] == ["failed", "infra_error"]
    assert step_results[0].termination == exit_error
    assert step_results[1].infra_reasons == (f"step_exception:{exit_error}",)


def test_a_step_without_agent_usage_is_still_graded() -> None:
    gathered = trial_evidence([StepOutcome("checkpoint_1")])
    assert gathered.result is not None
    item = gathered.result["step_results"][0]
    item["agent_result"] = None
    step_results = classify_evidence(classification_context(), gathered)
    assert step_results[0].usage is None
    assert step_results[0].checkpoint_state is CheckpointState.SOLVED


def test_trial_level_failures_fail_the_validity_gate() -> None:
    gathered, step_results = _classify([StepOutcome("checkpoint_1")], trial_exception="X")
    assert step_results[1].infra_reasons == ("not_started:X",)
    assert not _valid(gathered, step_results, CONTINUE)
    gathered, step_results = _classify(
        [StepOutcome("checkpoint_1")], steps=STEPS[:1], agent_version="0.1"
    )
    assert _valid(gathered, step_results, CONTINUE, runnable=1)
    for rule in (CONTINUE, HALT):
        gathered, step_results = _classify([], rule, result=False)
        # Environment start or step preparation failed: infrastructure, whatever the mode.
        assert _states(step_results) == ["infra_error"] * 8
        assert step_results[0].infra_reasons == ("not_started:no_result",)
        checks = {
            c.name: c
            for c in validity_from_evidence(
                gathered.result, (), step_results, "0.156.1", rule
            ).checks
        }
        assert not checks["trial_completed"].passed
        assert checks["trial_completed"].detail == "no result.json"
        assert not checks["steps_started"].passed
        assert reached(rule, step_results, None)[0] == (True,) + (False,) * 7


def test_a_trial_that_started_no_step_is_not_completed() -> None:
    gathered = trial_evidence([])
    checks = validity_from_evidence(gathered.result, (), (), "0.156.1", CONTINUE).checks
    completed = next(c for c in checks if c.name == "trial_completed")
    assert not completed.passed
    assert completed.detail == "no step started"


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
def test_the_harness_check_reads_what_harbor_installed(log: str, passed: bool, detail: str) -> None:
    """Harbor reports the configured version, so the install log is the evidence."""
    lines = [f"Running command: set -euo pipefail; {log}", "unrelated codex@9 line"] if log else []
    check = agent_version_check(
        {"agent_info": {"name": "codex", "version": "1"}}, "0.156.1", installed_versions(lines)
    )
    assert check.passed is passed
    assert detail in check.detail


def test_a_no_llm_override_has_no_harness_to_pin() -> None:
    check = agent_version_check(None, None, ["9.9"])
    assert check.passed
    assert "no agent CLI" in check.detail
    assert agent_version_check(None, "0.156.1", []).detail.startswith(
        "pinned '0.156.1'; agent None"
    )


def test_an_off_pin_install_fails_the_validity_gate() -> None:
    gathered, step_results = _classify([StepOutcome("checkpoint_1")], steps=STEPS[:1])
    assert not _valid(gathered, step_results, CONTINUE, runnable=1, installs=("0.157.0",))


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


def _outcomes(passes: list[bool | None]) -> list[StepOutcome]:
    return [
        StepOutcome(
            step,
            passed=passed if passed is not None else False,
            infra="sandbox_error" if passed is None else None,
        )
        for step, passed in zip(STEPS, passes, strict=True)
    ]


def _halt_run(passes: list[bool | None], rule: ProgressionRule) -> list[StepOutcome]:
    return _executed_under_gate(_outcomes(passes), rule)


@pytest.mark.parametrize(
    "passes",
    [
        [True, True, False, True, True, True, True, True],  # checkpoint 3 fails
        [True, None, True, True, True, True, True, True],  # checkpoint 2 has no verdict
        [True] * 8,
    ],
)
def test_derived_strict_reached_equals_the_halt_run(passes: list[bool | None]) -> None:
    _, continued = _classify(_outcomes(passes))
    halt_evidence, halted = _classify(_halt_run(passes, HALT), HALT)
    derived, _ = reached(CONTINUE, continued, None)
    halt_reached, source = reached(HALT, halted, None)
    assert source == "executed"
    assert derived == halt_reached
    if None not in passes:
        # A valid halt run executed exactly the strict-reached prefix.
        executed = tuple(a.checkpoint_state is not CheckpointState.NOT_ATTEMPTED for a in halted)
        assert executed == derived
        assert _valid(halt_evidence, halted, HALT)
    if passes[2] is False:
        assert derived == (True, True, True, False, False, False, False, False)
    # The halt prefix grades exactly like the continue run's first checkpoints.
    for a, b in zip(halted, continued, strict=True):
        if a.checkpoint_state is not CheckpointState.NOT_ATTEMPTED and a.termination != (
            "not_started"
        ):
            assert a.checkpoint_state == b.checkpoint_state


def test_reached_is_strict_whatever_the_halt_threshold() -> None:
    """min_reward < 1 halts later, but ``reached`` stays SCB-strict and equals the continue run."""
    partial = progression_rule(ProtocolSpec(progression="halt_on_failure", min_reward=0.5))
    # checkpoint_2 passes half its cases (0.5 >= min_reward): the halt run goes on.
    passes: list[bool | None] = [True, False, True, True, True, True, True, True]
    halt_evidence, halted = _classify(_halt_run(passes, partial), partial)
    assert sum(a.checkpoint_state is not CheckpointState.NOT_ATTEMPTED for a in halted) == 8
    _, continued = _classify([StepOutcome(s, passed=p) for s, p in zip(STEPS, passes, strict=True)])
    assert reached(partial, halted, None)[0] == reached(CONTINUE, continued, None)[0]
    assert reached(partial, halted, None)[0] == (True, True) + (False,) * 6
    assert _valid(halt_evidence, halted, partial)
    # Harbor running past its own gate (or stopping short of it) is not a valid halt run.
    short_evidence, short = _classify(_halt_run(passes, partial)[:3], partial)
    assert not _valid(short_evidence, short, partial)


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


def test_halt_gates_on_the_solvable_verdict_not_the_raw_grade() -> None:
    """A case the reference itself fails cannot stop a trial (a raw failure the set forgives)."""
    steps = [
        StepOutcome(step, passed=step != "checkpoint_2", forgiven=step == "checkpoint_2")
        for step in STEPS
    ]
    # Raw grade fails checkpoint_2, but its solvable set (case b only) passes: the gate holds.
    assert gate_rewards("checkpoint_2", steps[1])["strict_pass_rate"] == 0.5
    assert gate_rewards("checkpoint_2", steps[1])["solvable_pass_rate"] == 1.0
    halt_evidence, halted = _classify(steps, HALT)
    assert all(a.checkpoint_state is not CheckpointState.NOT_ATTEMPTED for a in halted)
    assert halted[1].checkpoint_state is CheckpointState.FAILED
    assert _valid(halt_evidence, halted, HALT)
    # Strict ``reached`` still stops at the raw failure.
    assert reached(HALT, halted, None)[0] == (True, True) + (False,) * 6


def test_halt_stops_where_the_solvable_set_fails() -> None:
    outcomes = _halt_run([True, False, True, True, True, True, True, True], HALT)
    assert [o.name for o in outcomes] == ["checkpoint_1", "checkpoint_2"]


# Lock-pinned excluded steps (empty solvable set): metric_transform_lang checkpoint_1 and
# mocked_http checkpoint_7 are pinned ``no_new_scoreable_tests``.
@pytest.mark.parametrize("at", [0, 6], ids=["mtl-checkpoint_1", "mocked_http-checkpoint_7"])
@pytest.mark.parametrize("raw_passed", [True, False], ids=["raw-pass", "raw-fail"])
def test_a_pinned_excluded_step_runs_without_gating_and_is_reported_excluded(
    at: int, raw_passed: bool
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

    halt_evidence, halted = _classify(outcomes, HALT)
    expected = ["solved"] * 8
    expected[at] = "excluded"
    assert _states(halted) == expected  # neither solved nor failed, and every step ran
    assert _valid(halt_evidence, halted, HALT)
    # The agent's workspace carries on from the excluded step.
    snapshot = halted[at].snapshot
    assert snapshot is not None
    if at < 7:
        assert halted[at + 1].parent_snapshot_digest == snapshot.digest
    # Raw strict ``reached`` keeps the excluded step's raw verdict.
    strict, source = reached(HALT, halted, None)
    assert source == "executed"
    assert strict == tuple(raw_passed or k <= at for k in range(8))

    _, continued = _classify(outcomes)
    assert _states(continued) == expected  # continue mode reports the same exclusion
    assert reached(CONTINUE, continued, None)[0] == strict


@pytest.mark.parametrize("failing", ["failed", "infra", "unpinned", "leaked"])
def test_halt_still_stops_where_there_is_no_pinned_exclusion(failing: str) -> None:
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
    _, halted = _classify(executed, HALT)
    assert halted[0].checkpoint_state is CheckpointState.EXCLUDED
    assert halted[1].checkpoint_state is (
        CheckpointState.FAILED if failing == "failed" else CheckpointState.INFRA_ERROR
    )
    expected = CheckpointState.NOT_ATTEMPTED if failing == "failed" else CheckpointState.INFRA_ERROR
    assert all(a.checkpoint_state is expected for a in halted[2:])


def test_the_reader_gathers_evidence_for_started_and_unstarted_steps(tmp_path: Path) -> None:
    document = harbor_result([StepOutcome("checkpoint_1", passed=None)])
    (tmp_path / "result.json").write_text(json.dumps(document))
    instruction = tmp_path / "steps" / "checkpoint_2" / "instruction.md"
    instruction.parent.mkdir(parents=True)
    instruction.write_bytes(b"next")

    gathered = read_evidence(tmp_path, classification_context(task_dir=tmp_path))

    assert set(gathered.steps) == set(STEPS)
    started = gathered.steps["checkpoint_1"]
    assert started.record is None
    assert started.record_path is None
    assert not started.probes.passed
    assert not started.idle_killed
    assert gathered.steps["checkpoint_2"].instruction_sha256 == hashlib.sha256(b"next").hexdigest()
    assert gathered.steps["checkpoint_2"].probes.passed  # never run: nothing to check
    assert gathered.seed_parent is None
    ctx = classification_context(task_dir=tmp_path)
    classified = classify_trial(tmp_path, ctx)
    assert classified[0].checkpoint_state is CheckpointState.INFRA_ERROR
    assert "no_verifier_record" in classified[0].infra_reasons
    assert all(step.termination == "not_started" for step in classified[1:])
    assert trial_exception(tmp_path) is None
    assert harness_installs(tmp_path) == []
    (tmp_path / "trial.log").write_text("Running command: npm install -g @openai/codex@9.0; fi\n")
    for pinned in ("0.156.1", None):
        validity = execution_validity(tmp_path, classified, pinned, CONTINUE)
        version = next(c for c in validity.checks if c.name == "agent_version")
        assert version.passed is (pinned is None)
    assert read_evidence(tmp_path, replace(ctx, planned_steps=())).seed_parent is None
