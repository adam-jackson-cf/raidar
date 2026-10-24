"""Planner (D1): every trial listed with its hashes, counterbalanced AB/BA order, selection."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.design.experiment.loader import load_experiment
from raidar.design.experiment.lock import LockError
from raidar.design.problem.store import ROOT_ENV
from raidar.engine.trial.plan import PlanError, PlanOptions, arm_order, build_plan
from raidar.subject.environment.python.harness import HarnessPinError
from raidar.subject.environment.python.models import load_pack_spec
from tests.execution_support import ImportedFixture, locked_experiment, validated_fixture


@pytest.fixture(scope="module")
def fixture_problem(tmp_path_factory: pytest.TempPathFactory) -> ImportedFixture:
    return validated_fixture(tmp_path_factory.mktemp("problems"))


@pytest.fixture
def locked(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, dict[str, Any], Path]:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    return locked_experiment(
        tmp_path / "exp", fixture_problem.ref, environment={"compose_init": False}
    )


def _plan(locked: tuple[Any, dict[str, Any], Path], fixture: ImportedFixture, **options: Any):
    loaded, lock, _ = locked
    return build_plan(
        loaded, lock, store=fixture.store, pack=load_pack_spec(), options=PlanOptions(**options)
    )


def test_plan_lists_every_arm_attempt_trial_with_input_hashes(
    locked: tuple[Any, dict[str, Any], Path], fixture_problem: ImportedFixture
) -> None:
    plan = _plan(locked, fixture_problem)
    loaded, lock, _ = locked
    spec = loaded.spec
    assert len(plan.trials) == len(spec.arms) * spec.analysis.attempts
    assert Counter(t.arm for t in plan.trials) == {"baseline": 5, "guided": 5}
    assert [t.sequence for t in plan.trials] == list(range(len(plan.trials)))
    assert len({t.hashes.trial for t in plan.trials}) == len(plan.trials)
    for trial in plan.trials:
        assert trial.hashes.lock == lock["lock_sha256"]
        assert trial.hashes.arm == lock["execution"]["arms"][trial.arm]["digest"]
        assert trial.hashes.problem == lock["execution"]["problems"][0]["digest"]
        assert trial.hashes.condition == plan.arms[trial.arm].compiled.digest
    # The baseline arm has no components but still has a compiled condition (its reset).
    baseline = plan.arms["baseline"].compiled
    assert not baseline.uses
    assert "raidar-step-reset" in baseline.setup_script()
    assert baseline.digest != plan.arms["guided"].compiled.digest
    # Replanning is deterministic.
    assert _plan(locked, fixture_problem).to_json() == plan.to_json()


@pytest.mark.parametrize("arms", [["a", "b"], ["a", "b", "c"], ["a", "b", "c", "d"]])
def test_arm_order_is_counterbalanced_seeded_and_reproducible(arms: list[str]) -> None:
    n = len(arms)
    attempts = 6 * n
    orders = [tuple(arm_order(20260929, attempt, 0, arms)) for attempt in range(attempts)]
    assert all(sorted(order) == arms for order in orders)
    # Every complete cycle of len(arms) attempts puts each arm in each position once
    # (AB/BA for two arms), so start-order effects cancel within the cycle.
    for start in range(0, attempts, n):
        cycle = orders[start : start + n]
        for position in range(n):
            assert sorted(order[position] for order in cycle) == arms
    assert orders == [tuple(arm_order(20260929, attempt, 0, arms)) for attempt in range(attempts)]
    # The lead arm of each cycle is drawn from the seed, not fixed.
    assert len({orders[start] for start in range(0, attempts, n)}) > 1
    other_seed = [tuple(arm_order(7, attempt, 0, arms)) for attempt in range(attempts)]
    assert other_seed != orders


def test_concurrency_is_per_machine_and_outside_trial_identity(
    locked: tuple[Any, dict[str, Any], Path], fixture_problem: ImportedFixture
) -> None:
    default = _plan(locked, fixture_problem)
    machine = _plan(locked, fixture_problem, concurrency=4)
    assert default.concurrency == locked[0].spec.analysis.concurrency
    assert machine.concurrency == 4
    assert [t.hashes for t in machine.trials] == [t.hashes for t in default.trials]
    assert machine.lock_sha256 == default.lock_sha256
    with pytest.raises(PlanError, match="concurrency"):
        _plan(locked, fixture_problem, concurrency=0)
    loaded, _, _ = locked
    runner = CliRunner()
    base = runner.invoke(main, ["plan", str(loaded.path), "--json"])
    other = runner.invoke(main, ["plan", str(loaded.path), "--json", "--concurrency", "3"])
    assert other.exit_code == 0, other.output  # no relock needed
    shown, other_shown = json.loads(base.output), json.loads(other.output)
    assert other_shown["concurrency"] == 3
    assert other_shown["trials"] == shown["trials"]


def test_selection_and_truncation_change_what_runs_not_the_condition(
    locked: tuple[Any, dict[str, Any], Path], fixture_problem: ImportedFixture
) -> None:
    full = _plan(locked, fixture_problem)
    assert full.selected == {t.trial_id for t in full.trials}
    subset = _plan(locked, fixture_problem, arms=("baseline",), attempts=(0,), max_checkpoints=1)
    assert [t.trial_id for t in subset.selected_trials()] == ["b00-p0-baseline"]
    assert len(subset.trials) == len(full.trials)
    trial = subset.selected_trials()[0]
    original = next(t for t in full.trials if t.trial_id == trial.trial_id)
    assert trial.steps == ("checkpoint_1",)
    assert original.steps == ("checkpoint_1", "checkpoint_2")
    assert trial.hashes.condition == original.hashes.condition
    assert trial.hashes.protocol != original.hashes.protocol
    assert trial.hashes.trial != original.hashes.trial
    with pytest.raises(PlanError, match="unknown arms"):
        _plan(locked, fixture_problem, arms=("nope",))
    with pytest.raises(PlanError, match="attempts"):
        _plan(locked, fixture_problem, attempts=(99,))


def test_agent_runs_the_pack_pinned_harness_with_the_arm_condition(
    locked: tuple[Any, dict[str, Any], Path], fixture_problem: ImportedFixture
) -> None:
    plan = _plan(locked, fixture_problem)
    config = plan.arms["guided"].agent_config
    assert config.name == "codex"
    assert config.model_name == "openai/gpt-6-luna"
    assert config.kwargs["version"] == "0.156.1"
    assert config.kwargs["reasoning_effort"] == "low"
    override = _plan(locked, fixture_problem, agent_override="oracle")
    assert override.arms["guided"].agent_config.name == "oracle"
    assert override.trials[0].hashes.agent != plan.trials[0].hashes.agent


def test_a_lock_that_no_longer_matches_the_experiment_is_refused(
    locked: tuple[Any, dict[str, Any], Path], fixture_problem: ImportedFixture
) -> None:
    loaded, lock, _ = locked
    changed = load_experiment(loaded.path, None)
    stale = json.loads(json.dumps(lock))
    stale["experiment"]["spec_sha256"] = "0" * 64
    with pytest.raises(PlanError, match="different experiment spec"):
        build_plan(changed, stale, store=fixture_problem.store, pack=load_pack_spec())
    stale = json.loads(json.dumps(lock))
    stale["execution"]["problems"][0]["digest"] = "0" * 64
    with pytest.raises(PlanError, match="changed since the lock"):
        build_plan(changed, stale, store=fixture_problem.store, pack=load_pack_spec())


def test_an_off_pin_harness_version_is_refused_before_any_trial(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    agents = [
        {
            "id": "luna",
            "name": "codex",
            "provider": "openai",
            "model": "gpt-6-luna",
            "version": "0.1.0",
        }
    ]
    # The lock refuses it (RT5), so no plan or job can exist for it; the plan's own
    # pin check (HarnessPinError) stays as the second line of defence.
    with pytest.raises(LockError, match=r"agents\.0\.version: \[RT5\] codex 0\.1\.0"):
        locked_experiment(tmp_path / "exp", fixture_problem.ref, agents=agents)
    assert issubclass(HarnessPinError, ValueError)


def test_plan_command_dry_run_and_stale_lock(
    locked: tuple[Any, dict[str, Any], Path], fixture_problem: ImportedFixture
) -> None:
    loaded, _, lock_path = locked
    runner = CliRunner()
    shown = runner.invoke(main, ["plan", str(loaded.path), "--arm", "baseline", "--attempt", "0"])
    assert shown.exit_code == 0, shown.output
    assert "1/10 trials selected" in shown.output
    assert shown.output.count("condition=") == 10
    as_json = runner.invoke(main, ["plan", str(loaded.path), "--json"])
    payload = json.loads(as_json.output)
    assert len(payload["trials"]) == 10
    assert payload["arms"]["baseline"]["condition_digest"]
    source = loaded.path.read_text()
    loaded.path.write_text(source.replace("baseline_arm: baseline", "baseline_arm: guided"))
    reanalysed = runner.invoke(main, ["plan", str(loaded.path)])
    assert reanalysed.exit_code != 0
    assert "Stale: analysis:" in reanalysed.output
    assert "execution:" not in reanalysed.output
    loaded.path.write_text(source.replace("attempts: 5", "attempts: 4"))
    stale = runner.invoke(main, ["plan", str(loaded.path)])
    assert stale.exit_code != 0
    assert "out of date" in stale.output
    assert "execution: execution inputs changed" in stale.output
    loaded.path.write_text(source)
    lock_path.write_text(lock_path.read_text().replace('"attempts": 5', '"attempts": 4'))
    tampered = runner.invoke(main, ["plan", str(loaded.path)])
    assert tampered.exit_code != 0
    assert "not a valid lock" in tampered.output
    lock_path.unlink()
    missing = runner.invoke(main, ["plan", str(loaded.path)])
    assert missing.exit_code != 0
    assert "raidar lock" in missing.output


def test_run_command_is_the_experiment_runner_only(tmp_path: Path) -> None:
    runner = CliRunner()
    removed = runner.invoke(main, ["run", "--scenario", str(tmp_path)])
    assert removed.exit_code != 0
    assert "No such option: --scenario" in removed.output
    help_text = runner.invoke(main, ["run", "--help"])
    assert help_text.exit_code == 0
    assert "--agent-override" in help_text.output


def test_a_lock_from_another_python_patch_release_still_plans(
    locked: tuple[Any, dict[str, Any], Path],
) -> None:
    from raidar.design.experiment.lock import render_lock, seal_lock

    loaded, lock, lock_path = locked
    sealed = {"sections", "lock_sha256", "document_sha256"}
    body = {key: value for key, value in lock.items() if key not in sealed}
    body["toolchain"] = {**lock["toolchain"], "python": "3.12.11"}
    other = seal_lock(body)
    assert other["lock_sha256"] == lock["lock_sha256"]
    lock_path.write_bytes(render_lock(other))

    result = CliRunner(mix_stderr=False).invoke(main, ["plan", str(loaded.path), "--json"])

    assert result.exit_code == 0, (result.output, result.stderr)
    assert "differs from the locked 3.12.11 (ignored)" in result.stderr
