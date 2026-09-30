"""Execution driver (D1 concurrency, D2): ordering, guard wiring, run directory records."""

from __future__ import annotations

import asyncio
import inspect
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner
from harbor.models.trial.config import AgentConfig, TrialConfig

from raidar.agents.config import Harness
from raidar.cli import main
from raidar.codex_auth import ResolvedCodexAuth
from raidar.execution import driver
from raidar.execution.agent_config import CODEX_AUTH_ENV, reported_harness_pin, with_host_auth
from raidar.execution.driver import TrialRun, run_harbor_trial, run_in_order
from raidar.execution.materialise import MaterialiseError, materialise_task
from raidar.execution.plan import PlanOptions, build_plan
from raidar.execution.run import RunContext, execute, load_problems
from raidar.oracle.verifier import RaidarOracleVerifier
from raidar.packs.python import load_pack_spec
from raidar.problems.rewrite import ORACLE_VERIFIER_IMPORT_PATH
from raidar.problems.store import ROOT_ENV
from tests.execution_support import (
    ImportedFixture,
    StepOutcome,
    locked_experiment,
    validated_fixture,
    write_trial,
)


def test_trials_start_in_plan_order_and_never_exceed_concurrency() -> None:
    running = 0
    peak = 0
    started: list[int] = []

    async def worker(job: int) -> int:
        nonlocal running, peak
        started.append(job)
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01 * (5 - job % 5))
        running -= 1
        return job * 10

    results = asyncio.run(run_in_order(list(range(12)), worker, concurrency=3))
    assert results == [job * 10 for job in range(12)]
    assert started == list(range(12))
    assert peak == 3
    serial = asyncio.run(run_in_order([1, 2], worker, concurrency=1))
    assert serial == [10, 20]
    with pytest.raises(ValueError, match="concurrency"):
        asyncio.run(run_in_order([1], worker, concurrency=0))


def test_a_raising_worker_starts_nothing_more_and_leaves_nothing_running() -> None:
    started: list[int] = []
    finished: list[int] = []

    async def worker(job: int) -> int:
        started.append(job)
        if job == 1:
            raise RuntimeError("Trial.create: docker unreachable")
        await asyncio.sleep(0.05)
        finished.append(job)
        return job

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="docker unreachable"):
            await run_in_order([0, 1, 2, 3, 4, 5], worker, concurrency=2)
        await asyncio.sleep(0.2)  # anything still running would finish here

    asyncio.run(scenario())
    assert 1 in started
    assert max(started) <= 3  # stopped starting jobs once the failure was seen
    assert set(finished) <= {0}  # siblings were cancelled, not orphaned


def test_the_leak_guard_is_installed_before_the_trial_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    class FakeTrial:
        class paths:  # noqa: N801 (mirrors Harbor's attribute)
            trial_dir = tmp_path / "t"

        async def run(self) -> Any:
            events.append("run")
            return type("Result", (), {"exception_info": None})()

    async def create(config: TrialConfig) -> FakeTrial:
        events.append("create")
        return FakeTrial()

    def guard(trial: Any, bundle: Any) -> None:
        events.append(f"guard:{bundle}")

    monkeypatch.setattr(driver.Trial, "create", create)
    monkeypatch.setattr(driver, "install_leak_guard", guard)
    outcome = asyncio.run(run_harbor_trial(TrialConfig.model_construct(), tmp_path / "bundle"))
    assert events == ["create", f"guard:{tmp_path / 'bundle'}", "run"]
    assert outcome == TrialRun(tmp_path / "t", None)


@pytest.fixture(scope="module")
def fixture_problem(tmp_path_factory: pytest.TempPathFactory) -> ImportedFixture:
    return validated_fixture(tmp_path_factory.mktemp("problems"))


def _plan(tmp_path: Path, fixture: ImportedFixture, **changes: Any) -> Any:
    loaded, lock, _ = locked_experiment(
        tmp_path / "exp",
        fixture.ref,
        runtime={"compose_init": False},
        assessment={
            "scorers": ["correctness@1"],
            "inventory_policy": {"generated_globs": ["gen/**"]},
        },
        **changes,
    )
    return build_plan(
        loaded,
        lock,
        store=fixture.store,
        pack=load_pack_spec(),
        options=PlanOptions(blocks=(0,)),
    )


def test_a_run_materialises_each_trial_and_writes_attempt_records(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    monkeypatch.setattr("raidar.execution.run.image_present", lambda tag: True)
    monkeypatch.setattr(
        "raidar.execution.trial_config.with_host_auth", lambda config, harness: config
    )
    protocol = {"progression": "halt_on_failure", "min_reward": 1.0}
    plan = _plan(tmp_path, fixture_problem, protocol=protocol)
    seen: list[TrialConfig] = []

    async def fake_runner(config: TrialConfig, bundle_root: Path) -> TrialRun:
        seen.append(config)
        outcomes = [StepOutcome("checkpoint_1", passed=False)]  # halts after checkpoint 1
        return TrialRun(write_trial(config.trials_dir / config.trial_name, outcomes), None)

    ctx = RunContext(
        plan=plan,
        run_dir=tmp_path / "runs" / plan.experiment_id / "r1",
        run_id="r1",
        problems=load_problems(plan, fixture_problem.store),
        runner=fake_runner,
        docker_context="orbstack",
    )
    records = asyncio.run(execute(ctx))
    assert [r.trial.trial_id for r in records] == [t.trial_id for t in plan.selected_trials()]
    config = seen[0]
    assert config.verifier.import_path == ORACLE_VERIFIER_IMPORT_PATH
    kwargs = config.verifier.kwargs
    assert kwargs["oracle_bundle"] == str(ctx.problems[0].bundle.root)
    assert kwargs["snapshot_source"] == "artifact"
    assert kwargs["docker_context"] == "orbstack"
    # The verifier kwargs are ones the Raidar oracle verifier accepts.
    assert set(kwargs) <= set(inspect.signature(RaidarOracleVerifier.__init__).parameters)
    assert config.agent.name == "codex"
    assert config.agent.kwargs["version"] == "0.156.1"
    task = Path(config.task.path or "")
    toml = tomllib.loads((task / "task.toml").read_text())
    assert [s["min_reward"] for s in toml["steps"]] == [{"strict_pass_rate": 1.0}] * 2
    assert (task / "steps" / "checkpoint_1" / "workdir" / "setup.sh").is_file()
    guided = next(c for c in seen if c.trial_name.endswith("guided"))
    assert (Path(guided.task.path or "") / "steps/checkpoint_1/workdir/AGENTS.md").is_file()
    record = next(r for r in records if r.trial.arm == "baseline")
    assert [a.checkpoint_state.value for a in record.attempts] == ["failed", "not_attempted"]
    # The synthetic trial holds no harness context, so the guided arm's rules-file
    # discovery probe fails: that step is infra_error and the trajectory is invalid.
    guided_record = next(r for r in records if r.trial.arm == "guided")
    assert guided_record.attempts[0].checkpoint_state.value == "infra_error"
    assert guided_record.attempts[0].infra_reasons[0].startswith("component_probe:rules-file@1")
    assert not guided_record.valid
    assert record.reached == (True, False)
    assert record.reached_source == "executed"
    assert record.valid
    phases = {p.phase for p in record.phases}
    assert {"component_apply", "per_step_reset", "raidar_result_write"} <= phases
    run_dir = ctx.run_dir
    stored = json.loads((run_dir / "trajectories" / f"{record.trial.trial_id}.json").read_text())
    assert stored["attempts"][0]["checkpoint_state"] == "failed"
    manifest = json.loads((run_dir / "run.json").read_text())
    assert manifest["partial"]
    assert manifest["summary"]["valid"] == 1
    assert json.loads((run_dir / "plan.json").read_text())["lock_sha256"] == plan.lock_sha256
    assessment = yaml.safe_load((run_dir / "assessment-config.yaml").read_text())
    assert assessment["inventory_policy"]["generated_globs"] == ["gen/**"]
    with pytest.raises(FileExistsError):
        asyncio.run(execute(ctx))


def test_a_trial_that_raises_is_recorded_as_infra_and_the_run_completes(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    monkeypatch.setattr("raidar.execution.run.image_present", lambda tag: True)
    monkeypatch.setattr(
        "raidar.execution.trial_config.with_host_auth", lambda config, harness: config
    )
    loaded, lock, _ = locked_experiment(
        tmp_path / "exp",
        fixture_problem.ref,
        runtime={"compose_init": False},
        analysis={"baseline_arm": "baseline", "blocks": 2, "seed": 1, "concurrency": 2},
    )
    plan = build_plan(
        loaded,
        lock,
        store=fixture_problem.store,
        pack=load_pack_spec(),
        options=PlanOptions(arms=("baseline",), max_checkpoints=1),
    )
    failing = plan.selected_trials()[0].trial_id

    async def fake_runner(config: TrialConfig, bundle_root: Path) -> TrialRun:
        if config.trial_name == failing:
            raise RuntimeError("Trial.create: docker unreachable")
        await asyncio.sleep(0.05)
        outcomes = [StepOutcome("checkpoint_1")]
        return TrialRun(write_trial(config.trials_dir / config.trial_name, outcomes), None)

    ctx = RunContext(
        plan=plan,
        run_dir=tmp_path / "run",
        run_id="r",
        problems=load_problems(plan, fixture_problem.store),
        runner=fake_runner,
    )
    records = {r.trial.trial_id: r for r in asyncio.run(execute(ctx))}
    assert set(records) == {t.trial_id for t in plan.selected_trials()}
    broken = records.pop(failing)
    assert [a.checkpoint_state.value for a in broken.attempts] == ["infra_error", "not_attempted"]
    assert broken.attempts[0].infra_reasons == ("runner_exception:RuntimeError",)
    assert broken.trial_exception == "RuntimeError"
    assert not broken.valid
    assert "docker unreachable" in broken.validity.checks[0].detail
    assert (ctx.trials_dir / failing / "raidar-runner-error.txt").is_file()
    assert (ctx.run_dir / "trajectories" / f"{failing}.json").is_file()
    (sibling,) = records.values()
    assert sibling.valid
    manifest = json.loads((ctx.run_dir / "run.json").read_text())
    assert manifest["finished_at"]
    assert "aborted" not in manifest
    assert manifest["summary"]["trajectories"] == 2
    assert manifest["summary"]["valid"] == 1


def test_oracle_override_carries_reference_solutions_only_then(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    monkeypatch.setattr("raidar.execution.run.image_present", lambda tag: False)
    loaded, lock, _ = locked_experiment(
        tmp_path / "exp", fixture_problem.ref, runtime={"compose_init": False}
    )
    plan = build_plan(
        loaded,
        lock,
        store=fixture_problem.store,
        pack=load_pack_spec(),
        options=PlanOptions(
            arms=("baseline",), blocks=(0,), max_checkpoints=1, agent_override="oracle"
        ),
    )
    tasks: list[Path] = []

    async def fake_runner(config: TrialConfig, bundle_root: Path) -> TrialRun:
        tasks.append(Path(config.task.path or ""))
        assert config.agent.name == "oracle"
        return TrialRun(
            write_trial(config.trials_dir / config.trial_name, [StepOutcome("checkpoint_1")]),
            None,
        )

    ctx = RunContext(
        plan=plan,
        run_dir=tmp_path / "run",
        run_id="r",
        problems=load_problems(plan, fixture_problem.store),
        runner=fake_runner,
    )
    (record,) = asyncio.run(execute(ctx))
    assert (tasks[0] / "steps/checkpoint_1/solution/solve.sh").is_file()
    assert not (tasks[0] / "steps/checkpoint_2").exists()
    assert record.agent_override == "oracle"
    assert record.truncated_to == 1
    assert record.reached == (True, False)
    assert next(p for p in record.phases if p.phase == "environment_start").condition == "cold"


def test_materialising_refuses_drift_reuse_and_a_compose_mismatch(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    plan = _plan(tmp_path, fixture_problem)
    trial = plan.selected_trials()[0]
    source = fixture_problem.store.load(plan.problems[0].ref).task_dir
    arguments = {"steps": trial.steps, "rule": plan.rule}
    with pytest.raises(MaterialiseError, match="compiles to"):
        materialise_task(
            source, tmp_path / "a", condition=(plan.loaded, trial.arm, "0" * 64), **arguments
        )
    with pytest.raises(MaterialiseError, match="never reused"):
        materialise_task(
            source, tmp_path / "a", condition=(plan.loaded, trial.arm, "0" * 64), **arguments
        )
    with pytest.raises(MaterialiseError, match="no steps"):
        materialise_task(
            source,
            tmp_path / "b",
            steps=("checkpoint_9",),
            rule=plan.rule,
            condition=(plan.loaded, trial.arm, trial.hashes.condition),
        )
    init_loaded, _, _ = locked_experiment(tmp_path / "init", fixture_problem.ref)
    with pytest.raises(MaterialiseError, match="compose init"):
        materialise_task(
            source, tmp_path / "c", condition=(init_loaded, trial.arm, "x"), **arguments
        )


def test_codex_gets_the_host_session_file_path_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = tmp_path / "auth.json"
    session = ResolvedCodexAuth("auto", "chatgpt", auth, "file")
    monkeypatch.setattr("raidar.execution.agent_config.resolve_codex_auth", lambda: session)
    codex = AgentConfig(name="codex")
    assert with_host_auth(codex, Harness.CODEX_CLI).env == {CODEX_AUTH_ENV: str(auth)}
    assert with_host_auth(AgentConfig(name="oracle"), Harness.CODEX_CLI).env == {}
    wrapper = AgentConfig(
        import_path="raidar.components.review_loop:ReviewLoopAgent",
        kwargs={"inner_agent": {"name": "codex", "kwargs": {"version": "0.156.1"}}},
    )
    # A workflow wrapper is held to its wrapped harness's pin; a no-LLM override has none.
    assert reported_harness_pin(wrapper) == "0.156.1"
    assert reported_harness_pin(AgentConfig(name="codex", kwargs={"version": "0.156.1"})) == (
        "0.156.1"
    )
    assert reported_harness_pin(AgentConfig(name="oracle")) is None
    api = ResolvedCodexAuth("auto", "api", None, "OPENAI_API_KEY")
    monkeypatch.setattr("raidar.execution.agent_config.resolve_codex_auth", lambda: api)
    assert with_host_auth(codex, Harness.CODEX_CLI).env == {}


def test_run_command_runs_the_selection_into_the_runs_dir(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    loaded, _, _ = locked_experiment(
        tmp_path / "exp", fixture_problem.ref, runtime={"compose_init": False}
    )
    contexts: list[RunContext] = []

    async def fake_execute(ctx: RunContext) -> list[Any]:
        contexts.append(ctx)
        return []

    monkeypatch.setattr("raidar.commands.execution.execute", fake_execute)
    runner = CliRunner()
    arguments = ["run", str(loaded.path), "--arm", "baseline", "--block", "1"]
    result = runner.invoke(
        main, [*arguments, "--runs-dir", str(tmp_path / "runs"), "--run-id", "x"]
    )
    assert result.exit_code == 0, result.output
    (ctx,) = contexts
    assert ctx.run_dir == (tmp_path / "runs" / loaded.spec.id / "x").resolve()
    assert [t.trial_id for t in ctx.plan.selected_trials()] == ["b01-p0-baseline"]
    assert set(ctx.problems) == {0}
    none = runner.invoke(main, ["run", str(loaded.path), "--arm", "baseline", "--block", "7"])
    assert none.exit_code != 0
