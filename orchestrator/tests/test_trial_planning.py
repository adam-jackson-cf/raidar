"""Planner (D1): every trial listed with its hashes, counterbalanced AB/BA order, selection."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from raidar.cli import main
from raidar.contract.canonical import digest_json
from raidar.design.experiment.loader import load_experiment
from raidar.design.experiment.lock import LockError
from raidar.design.experiment.schema import ProblemEntry
from raidar.design.problem.store import ROOT_ENV
from raidar.engine.trial.agent_config import arm_agent_config
from raidar.engine.trial.plan import PlanError, PlanOptions, arm_order, build_plan
from raidar.subject.environment.python.harness import HarnessPinError
from raidar.subject.environment.python.pack_definition import load_pack_spec
from raidar.subject.workflow.runner import WorkflowRunner
from tests.execution_support import ImportedFixture, locked_experiment, validated_fixture
from tests.problems_support import ONESHOT_REF, build_fixture_store, record_validation


@pytest.fixture(scope="module")
def fixture_problem(tmp_path_factory: pytest.TempPathFactory) -> ImportedFixture:
    return validated_fixture(tmp_path_factory.mktemp("problems"))


@pytest.fixture
def locked(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, dict[str, Any], Path]:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    return locked_experiment(
        tmp_path / "exp", fixture_problem.ref, kind="smoke", environment={"compose_init": False}
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
    # The trial hash is content-addressed: arms with one identity share it per attempt.
    assert len({t.hashes.trial for t in plan.trials}) == spec.analysis.attempts
    for trial in plan.trials:
        arm = plan.arms[trial.arm].arm
        assert trial.hashes.lock == lock["lock_sha256"]
        assert trial.hashes.arm == lock["execution"]["arms"][trial.arm]["digest"]
        assert trial.hashes.problem == lock["execution"]["problems"][0]["digest"]
        assert trial.hashes.workflow == arm.definition.digest
        assert trial.hashes.stage_code == digest_json(arm.stage_code)
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
    runner = CliRunner(mix_stderr=False)
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
    assert trial.hashes.arm == original.hashes.arm
    assert trial.hashes.protocol != original.hashes.protocol
    assert trial.hashes.trial != original.hashes.trial
    with pytest.raises(PlanError, match="unknown arms"):
        _plan(locked, fixture_problem, arms=("nope",))
    with pytest.raises(PlanError, match="attempts"):
        _plan(locked, fixture_problem, attempts=(99,))


def _bound(tmp_path: Path, fixture: ImportedFixture, name: str, **binding: Any):
    return locked_experiment(
        tmp_path / name,
        fixture.ref,
        problems=[{"ref": fixture.ref, **binding}],
        environment={"compose_init": False},
    )


def test_a_checkpoint_subset_binding_locks_and_runs_the_first_k_as_one_task(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    _, full_lock, _ = _bound(tmp_path, fixture_problem, "full")
    loaded, subset_lock, _ = _bound(tmp_path, fixture_problem, "subset", checkpoints=1)

    assert full_lock["execution"]["problems"][0]["checkpoints"] == 2
    assert subset_lock["execution"]["problems"][0]["checkpoints"] == 1
    assert subset_lock["lock_sha256"] != full_lock["lock_sha256"]
    plan = build_plan(loaded, subset_lock, store=fixture_problem.store, pack=load_pack_spec())
    assert {trial.steps for trial in plan.trials} == {("checkpoint_1",)}
    assert all(target.steps == ("checkpoint_1",) for target in plan.problems)


def test_a_subset_binding_the_lock_does_not_pin_is_refused(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    _, full_lock, _ = _bound(tmp_path, fixture_problem, "full")
    loaded, _, _ = _bound(tmp_path, fixture_problem, "subset", checkpoints=1)

    with pytest.raises(PlanError, match="relock"):
        build_plan(loaded, full_lock, store=fixture_problem.store, pack=load_pack_spec())


def test_a_subset_larger_than_the_problem_or_off_a_path_does_not_lock(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    with pytest.raises(LockError, match="first 3 checkpoint"):
        _bound(tmp_path, fixture_problem, "big", checkpoints=3)
    for bad in (0, -1, True, 1.5):
        with pytest.raises(ValidationError):
            ProblemEntry.model_validate({"ref": fixture_problem.ref, "checkpoints": bad})
    with pytest.raises(ValidationError, match="local 'path'"):
        ProblemEntry.model_validate({"path": "tasks/x", "checkpoints": 1})


def test_a_one_checkpoint_reference_plans_but_cannot_take_a_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = build_fixture_store(tmp_path / "problems")
    record_validation(store, ONESHOT_REF, tmp_path / "validation")
    monkeypatch.setenv(ROOT_ENV, str(store.root))
    loaded, lock, _ = locked_experiment(
        tmp_path / "one", ONESHOT_REF, environment={"compose_init": False}
    )
    plan = build_plan(loaded, lock, store=store, pack=load_pack_spec())
    assert {trial.steps for trial in plan.trials} == {("checkpoint_1",)}
    with pytest.raises(LockError):
        locked_experiment(
            tmp_path / "prefix",
            ONESHOT_REF,
            problems=[{"ref": ONESHOT_REF, "checkpoints": 1}],
            environment={"compose_init": False},
        )


def test_the_max_checkpoints_override_is_for_smoke_experiments_only(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    loaded, lock, _ = locked_experiment(
        tmp_path / "ab", fixture_problem.ref, environment={"compose_init": False}
    )

    with pytest.raises(PlanError, match="smoke"):
        build_plan(
            loaded,
            lock,
            store=fixture_problem.store,
            pack=load_pack_spec(),
            options=PlanOptions(max_checkpoints=1),
        )


def test_agent_runs_the_pack_pinned_harness_as_a_workflow_runner_around_the_native_agent(
    locked: tuple[Any, dict[str, Any], Path], fixture_problem: ImportedFixture
) -> None:
    plan = _plan(locked, fixture_problem)
    config = plan.arms["guided"].agent_config
    assert config.import_path == WorkflowRunner.import_path()
    assert config.model_name == "openai/gpt-6-luna"
    assert config.kwargs["workflow"] == "one-shot@1"
    native = config.kwargs["inner_agent"]
    assert (native["name"], native["kwargs"]["version"]) == ("codex", "0.156.1")
    assert native["kwargs"]["reasoning_effort"] == "low"
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


def test_an_agent_release_off_the_pack_pin_is_refused_before_any_trial(
    locked: tuple[Any, dict[str, Any], Path],
    fixture_problem: ImportedFixture,
) -> None:
    plan = _plan(locked, fixture_problem)
    pack = load_pack_spec()
    pin = pack.image.harnesses["codex-cli"]
    moved = pack.model_copy(
        update={
            "image": pack.image.model_copy(
                update={"harnesses": {"codex-cli": pin.model_copy(update={"version": "0.1.0"})}}
            )
        }
    )
    with pytest.raises(HarnessPinError, match="0.1.0"):
        arm_agent_config(plan.arms["guided"].arm, moved, agent_timeout_sec=60)


def test_plan_command_dry_run_and_stale_lock(
    locked: tuple[Any, dict[str, Any], Path], fixture_problem: ImportedFixture
) -> None:
    loaded, _, lock_path = locked
    runner = CliRunner(mix_stderr=False)
    as_json = runner.invoke(main, ["plan", str(loaded.path), "--json"])
    assert as_json.exit_code == 0, as_json.output
    payload = json.loads(as_json.stdout)
    assert len(payload["trials"]) == 10
    assert payload["arms"]["baseline"]["workflow_sha256"]
    source = loaded.path.read_text()
    loaded.path.write_text(source.replace("baseline_arm: baseline", "baseline_arm: guided"))
    reanalysed = runner.invoke(main, ["plan", str(loaded.path)])
    assert reanalysed.exit_code != 0
    loaded.path.write_text(source.replace("attempts: 5", "attempts: 4"))
    stale = runner.invoke(main, ["plan", str(loaded.path)])
    assert stale.exit_code != 0
    loaded.path.write_text(source)
    lock_path.write_text(lock_path.read_text().replace('"attempts": 5', '"attempts": 4'))
    tampered = runner.invoke(main, ["plan", str(loaded.path)])
    assert tampered.exit_code != 0
    lock_path.unlink()
    missing = runner.invoke(main, ["plan", str(loaded.path)])
    assert missing.exit_code != 0


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
