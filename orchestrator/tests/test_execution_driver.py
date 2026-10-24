"""Execution driver (D1 concurrency, D2): ordering, guard wiring, job directory records."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner
from harbor.models.trial.config import AgentConfig, TrialConfig

from raidar.cli import main
from raidar.design.experiment.lock import lock_integrity_issues, spec_source_digest
from raidar.design.problem.rewrite import VERIFIER_IMPORT_PATH
from raidar.design.problem.store import ROOT_ENV
from raidar.engine.health.records_manifest import RECORDS_MANIFEST, tamper_findings
from raidar.engine.job.context import JobContext
from raidar.engine.job.plan import PlanOptions, build_plan
from raidar.engine.job.run import execute, load_problems
from raidar.engine.target.config import Selection, implicit_target
from raidar.engine.target.guard import REAP_FILE, TargetGuard, TargetLostError
from raidar.engine.target.health import TargetUnavailableError
from raidar.engine.target.job import JobTarget
from raidar.engine.trial import driver
from raidar.engine.trial.agent_config import CODEX_AUTH_ENV, reported_harness_pin, with_host_auth
from raidar.engine.trial.driver import TrialRun, run_harbor_trial, run_in_order
from raidar.engine.trial.environment import (
    RUN_SCOPED_DOCKER_IMPORT_PATH,
    RunScopedDockerEnvironment,
    run_scope,
)
from raidar.engine.trial.materialise import MaterialiseError, materialise_task
from raidar.engine.trial.config import harbor_trial_config
from raidar.grading.verifier import RaidarVerifier
from raidar.subject.agent.codex_auth import ResolvedCodexAuth
from raidar.subject.agent.config import Harness
from raidar.subject.environment.python.models import load_pack_spec
from raidar.subject.treatment.runtime.provision import Provision
from tests.execution_support import (
    ImportedFixture,
    StepOutcome,
    locked_experiment,
    validated_fixture,
    write_trial,
)

SELECTION = Selection.of(implicit_target("orbstack"))


def _no_reap(trial_id: str, trials_dir: Path) -> dict[str, Any]:
    return {}


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
    monkeypatch.setattr(
        driver, "install_agent_reaper", lambda trial, bundle: events.append("reaper")
    )
    monkeypatch.setattr(
        driver,
        "install_trial_provisioning",
        lambda trial, provisions: events.append(f"provision:{len(provisions)}"),
    )
    monkeypatch.setattr(
        driver, "install_tool_tree_watch", lambda trial, bundle: events.append("tool-tree")
    )
    monkeypatch.setattr(driver, "install_liveness_monitor", lambda trial: events.append("liveness"))
    provisions = (Provision("00-lint-tool", "lint-tool@2", "true", "true"),)
    outcome = asyncio.run(
        run_harbor_trial(TrialConfig.model_construct(), tmp_path / "bundle", provisions)
    )
    assert events == [
        "create",
        f"guard:{tmp_path / 'bundle'}",
        "reaper",
        "provision:1",
        "tool-tree",
        "liveness",
        "run",
    ]
    assert outcome == TrialRun(tmp_path / "t", None)


@pytest.fixture(scope="module")
def fixture_problem(tmp_path_factory: pytest.TempPathFactory) -> ImportedFixture:
    return validated_fixture(tmp_path_factory.mktemp("problems"))


def _plan(tmp_path: Path, fixture: ImportedFixture, **changes: Any) -> Any:
    loaded, lock, _ = locked_experiment(
        tmp_path / "exp",
        fixture.ref,
        environment={"compose_init": False},
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
        options=PlanOptions(attempts=(0,)),
    )


@pytest.mark.parametrize(
    ("protocol", "coverage", "timeout"),
    [({}, True, 2 * 900 + 600), ({"verifier_coverage": "offline"}, False, 900 + 600)],
)
def test_the_verifier_runs_the_coverage_pass_unless_the_protocol_defers_it(
    tmp_path: Path,
    fixture_problem: ImportedFixture,
    monkeypatch: pytest.MonkeyPatch,
    protocol: dict[str, str],
    coverage: bool,
    timeout: int,
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    monkeypatch.setattr(
        "raidar.engine.trial.config.with_host_auth", lambda config, harness: config
    )
    plan = _plan(tmp_path, fixture_problem, protocol=protocol)

    config = harbor_trial_config(
        plan,
        plan.selected_trials()[0],
        tmp_path / "task",
        trials_dir=tmp_path / "trials",
        bundle_root=tmp_path / "bundle",
        docker_context="orbstack",
    )

    assert config.verifier.kwargs["coverage"] is coverage
    assert config.verifier.override_timeout_sec == timeout


def test_a_run_materialises_each_trial_and_writes_step_results(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    monkeypatch.setattr("raidar.engine.trial.materialise.image_present", lambda tag: True)
    monkeypatch.setattr(
        "raidar.engine.trial.config.with_host_auth", lambda config, harness: config
    )
    protocol = {"progression": "halt_on_failure", "min_reward": 1.0}
    plan = _plan(tmp_path, fixture_problem, protocol=protocol)
    seen: list[TrialConfig] = []

    async def fake_runner(
        config: TrialConfig, bundle_root: Path, provisions: Sequence[Provision] = ()
    ) -> TrialRun:
        seen.append(config)
        outcomes = [StepOutcome("checkpoint_1", passed=False)]  # halts after checkpoint 1
        return TrialRun(write_trial(config.trials_dir / config.trial_name, outcomes), None)

    ctx = JobContext(
        plan=plan,
        job_dir=tmp_path / "jobs" / plan.experiment_id / "r1",
        job_id="r1",
        problems=load_problems(plan, fixture_problem.store),
        target=JobTarget(SELECTION),
        runner=fake_runner,
    )
    records = asyncio.run(execute(ctx))
    assert [r.trial.trial_id for r in records] == [t.trial_id for t in plan.selected_trials()]
    config = seen[0]
    assert config.verifier.import_path == VERIFIER_IMPORT_PATH
    kwargs = config.verifier.kwargs
    assert kwargs["oracle_bundle"] == str(ctx.problems[0].bundle.root)
    assert kwargs["snapshot_source"] == "artifact"
    assert kwargs["docker_context"] == "orbstack"
    # The Compose project is scoped to this job directory (no cross-run collision).
    assert (config.environment.import_path, config.environment.kwargs) == (
        RUN_SCOPED_DOCKER_IMPORT_PATH,
        {"run_scope": run_scope(ctx.trials_dir)},
    )
    # The verifier kwargs are ones the Raidar verifier accepts.
    assert set(kwargs) <= set(inspect.signature(RaidarVerifier.__init__).parameters)
    assert config.agent.name == "codex"
    assert config.agent.kwargs["version"] == "0.156.1"
    task = Path(config.task.path or "")
    toml = tomllib.loads((task / "task.toml").read_text())
    assert [s["min_reward"] for s in toml["steps"]] == [{"strict_pass_rate": 1.0}] * 2
    assert (task / "steps" / "checkpoint_1" / "workdir" / "setup.sh").is_file()
    guided = next(c for c in seen if c.trial_name.endswith("guided"))
    assert (Path(guided.task.path or "") / "steps/checkpoint_1/workdir/AGENTS.md").is_file()
    record = next(r for r in records if r.trial.arm == "baseline")
    assert [a.checkpoint_state.value for a in record.step_results] == ["failed", "not_attempted"]
    # The synthetic trial holds no harness context, so the guided arm's rules-file
    # discovery probe fails: that step is infra_error and the trial is invalid.
    guided_record = next(r for r in records if r.trial.arm == "guided")
    assert guided_record.step_results[0].checkpoint_state.value == "infra_error"
    assert guided_record.step_results[0].infra_reasons[0].startswith("component_probe:rules-file@1")
    assert not guided_record.valid
    assert record.reached == (True, False)
    assert record.reached_source == "executed"
    assert record.valid
    phases = {p.phase for p in record.phases}
    assert {"component_apply", "per_step_reset", "raidar_result_write"} <= phases
    job_dir = ctx.job_dir
    stored = json.loads((job_dir / "trial-records" / f"{record.trial.trial_id}.json").read_text())
    assert stored["step_results"][0]["checkpoint_state"] == "failed"
    manifest = json.loads((job_dir / "job.json").read_text())
    assert (manifest["partial"], manifest["summary"]["valid"]) == (True, 1)
    _assert_provenance(manifest, plan.concurrency)
    _assert_job_provenance(manifest, job_dir)
    assert json.loads((job_dir / "plan.json").read_text())["lock_sha256"] == plan.lock_sha256
    _assert_run_keeps_its_lock_and_settings(job_dir, plan.lock_sha256)
    with pytest.raises(FileExistsError):
        asyncio.run(execute(ctx))


def _assert_job_provenance(manifest: dict[str, Any], job_dir: Path) -> None:
    runner = manifest["runner"]
    assert set(runner) == {"commit", "dirty", "toolchain_sha256"}
    assert runner["toolchain_sha256"] == spec_source_digest()
    assert set(manifest["recipes"]) == {"pack", "graders"}
    assert all(len(digest) == 16 for digest in manifest["recipes"]["graders"].values())
    limits = manifest["effective_limits"]
    assert limits["agent_s"] == {"baseline": 1800.0, "guided": 1800.0}
    assert limits["verifier_s"] == 2 * 900 + 600
    assert limits["multipliers"]["timeout_multiplier"] == 1.0
    sealed = manifest["records_manifests"]
    assert set(sealed) == set(manifest["selected"])
    for trial, digest in sealed.items():
        trial_dir = job_dir / "trials" / trial
        assert hashlib.sha256((trial_dir / RECORDS_MANIFEST).read_bytes()).hexdigest() == digest
        files = json.loads((trial_dir / RECORDS_MANIFEST).read_text())["files"]
        assert f"trial-records/{trial}.json" in files
        assert all(not name.endswith(RECORDS_MANIFEST) for name in files)
        assert (
            tamper_findings(trial_dir, job_dir / "trial-records" / f"{trial}.json", job_dir, digest)
            == []
        )


def _assert_provenance(manifest: dict[str, Any], concurrency: int) -> None:
    assert manifest["mechanics_epoch"] == "mechanics-v1"
    assert "docker_context" not in manifest
    assert manifest["execution_target"] == {
        "name": "default",
        "kind": "docker",
        "docker_context": "orbstack",
        "trusted": True,
        "transport": None,
        "max_concurrency": None,
        "effective_concurrency": concurrency,
        "docker": None,
        "grader": {"name": "default", "docker_context": "orbstack"},
    }


def test_a_lost_target_marks_the_trial_and_aborts_the_job_before_the_next_trial(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    monkeypatch.setattr("raidar.engine.trial.materialise.image_present", lambda tag: True)
    monkeypatch.setattr(
        "raidar.engine.trial.config.with_host_auth", lambda config, harness: config
    )
    loaded, lock, _ = locked_experiment(
        tmp_path / "exp",
        fixture_problem.ref,
        environment={"compose_init": False},
        analysis={"baseline_arm": "baseline", "attempts": 2, "seed": 1, "concurrency": 1},
    )
    plan = build_plan(
        loaded,
        lock,
        store=fixture_problem.store,
        pack=load_pack_spec(),
        options=PlanOptions(arms=("baseline",), max_checkpoints=1),
    )
    first, second = (t.trial_id for t in plan.selected_trials())
    up = [True]
    started: list[str] = []

    def ensure() -> None:
        if not up[0]:
            raise TargetUnavailableError("daemon down")

    async def fake_runner(
        config: TrialConfig, bundle_root: Path, provisions: Sequence[Provision] = ()
    ) -> TrialRun:
        started.append(config.trial_name)
        up[0] = False  # the daemon goes away during the first trial
        raise RuntimeError("compose up failed: cannot connect to the Docker daemon")

    guard = TargetGuard(
        target=SELECTION.trial,
        healthy=lambda: up[0],
        ensure=ensure,
        reap=_no_reap,
        reconnect_s=0.0,
    )
    ctx = JobContext(
        plan=plan,
        job_dir=tmp_path / "run",
        job_id="r",
        problems=load_problems(plan, fixture_problem.store),
        target=JobTarget(SELECTION, None, guard),
        runner=fake_runner,
    )

    with pytest.raises(TargetLostError):
        asyncio.run(execute(ctx))

    assert started == [first]  # nothing more was scheduled
    stored = json.loads((ctx.job_dir / "trial-records" / f"{first}.json").read_text())
    checks = {check["name"]: check for check in stored["validity"]["checks"]}
    assert checks["target_reachable"]["passed"] is False
    assert "agent_idle" not in checks  # an idle kill is classified like an agent timeout
    assert json.loads((ctx.trials_dir / first / REAP_FILE).read_text())["reconnected"] is False
    assert not (ctx.job_dir / "trial-records" / f"{second}.json").exists()
    assert json.loads((ctx.job_dir / "job.json").read_text())["aborted"] == "TargetLostError"


def _assert_run_keeps_its_lock_and_settings(job_dir: Path, lock_sha256: str) -> None:
    """The run keeps the lock it executed under, so review/analysis still find it
    after the experiment's lock is relocked or overwritten, and its assessor settings."""
    kept = json.loads((job_dir / "job.lock.json").read_text())
    assert kept["lock_sha256"] == lock_sha256
    assert not lock_integrity_issues(kept)
    assessment = yaml.safe_load((job_dir / "assessment-config.yaml").read_text())
    assert assessment["inventory_policy"]["generated_globs"] == ["gen/**"]


def test_a_trial_that_raises_is_recorded_as_infra_and_the_run_completes(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    monkeypatch.setattr("raidar.engine.trial.materialise.image_present", lambda tag: True)
    monkeypatch.setattr(
        "raidar.engine.trial.config.with_host_auth", lambda config, harness: config
    )
    loaded, lock, _ = locked_experiment(
        tmp_path / "exp",
        fixture_problem.ref,
        environment={"compose_init": False},
        analysis={"baseline_arm": "baseline", "attempts": 2, "seed": 1, "concurrency": 2},
    )
    plan = build_plan(
        loaded,
        lock,
        store=fixture_problem.store,
        pack=load_pack_spec(),
        options=PlanOptions(arms=("baseline",), max_checkpoints=1),
    )
    failing = plan.selected_trials()[0].trial_id

    async def fake_runner(
        config: TrialConfig, bundle_root: Path, provisions: Sequence[Provision] = ()
    ) -> TrialRun:
        if config.trial_name == failing:
            raise RuntimeError("Trial.create: docker unreachable")
        await asyncio.sleep(0.05)
        outcomes = [StepOutcome("checkpoint_1")]
        return TrialRun(write_trial(config.trials_dir / config.trial_name, outcomes), None)

    ctx = JobContext(
        plan=plan,
        job_dir=tmp_path / "run",
        job_id="r",
        problems=load_problems(plan, fixture_problem.store),
        target=JobTarget(SELECTION),
        runner=fake_runner,
    )
    records = {r.trial.trial_id: r for r in asyncio.run(execute(ctx))}
    assert set(records) == {t.trial_id for t in plan.selected_trials()}
    broken = records.pop(failing)
    assert [a.checkpoint_state.value for a in broken.step_results] == [
        "infra_error",
        "not_attempted",
    ]
    assert broken.step_results[0].infra_reasons == ("runner_exception:RuntimeError",)
    assert broken.trial_exception == "RuntimeError"
    assert not broken.valid
    assert "docker unreachable" in broken.validity.checks[0].detail
    assert (ctx.trials_dir / failing / "raidar-runner-error.txt").is_file()
    assert (ctx.job_dir / "trial-records" / f"{failing}.json").is_file()
    (sibling,) = records.values()
    assert sibling.valid
    manifest = json.loads((ctx.job_dir / "job.json").read_text())
    assert manifest["finished_at"]
    assert "aborted" not in manifest
    assert manifest["summary"]["trials"] == 2
    assert manifest["summary"]["valid"] == 1


def test_verifier_override_carries_reference_solutions_only_then(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    monkeypatch.setattr("raidar.engine.trial.materialise.image_present", lambda tag: False)
    loaded, lock, _ = locked_experiment(
        tmp_path / "exp", fixture_problem.ref, environment={"compose_init": False}
    )
    plan = build_plan(
        loaded,
        lock,
        store=fixture_problem.store,
        pack=load_pack_spec(),
        options=PlanOptions(
            arms=("baseline",), attempts=(0,), max_checkpoints=1, agent_override="oracle"
        ),
    )
    tasks: list[Path] = []

    async def fake_runner(
        config: TrialConfig, bundle_root: Path, provisions: Sequence[Provision] = ()
    ) -> TrialRun:
        tasks.append(Path(config.task.path or ""))
        assert config.agent.name == "oracle"
        return TrialRun(
            write_trial(config.trials_dir / config.trial_name, [StepOutcome("checkpoint_1")]),
            None,
        )

    ctx = JobContext(
        plan=plan,
        job_dir=tmp_path / "run",
        job_id="r",
        problems=load_problems(plan, fixture_problem.store),
        target=JobTarget(SELECTION),
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
    monkeypatch.setattr("raidar.engine.trial.agent_config.resolve_codex_auth", lambda: session)
    codex = AgentConfig(name="codex")
    assert with_host_auth(codex, Harness.CODEX_CLI).env == {CODEX_AUTH_ENV: str(auth)}
    assert with_host_auth(AgentConfig(name="oracle"), Harness.CODEX_CLI).env == {}
    wrapper = AgentConfig(
        import_path="raidar.subject.treatment.runtime.review_loop:ReviewLoopAgent",
        kwargs={"inner_agent": {"name": "codex", "kwargs": {"version": "0.156.1"}}},
    )
    # A workflow wrapper is held to its wrapped harness's pin; a no-LLM override has none.
    assert reported_harness_pin(wrapper) == "0.156.1"
    assert reported_harness_pin(AgentConfig(name="codex", kwargs={"version": "0.156.1"})) == (
        "0.156.1"
    )
    assert reported_harness_pin(AgentConfig(name="oracle")) is None
    # The wrapper passes its env to every inner session (implement/review/fix): without
    # the session file its inner codex called the API with an empty key (401, pilot).
    assert with_host_auth(wrapper, Harness.CODEX_CLI).env == {CODEX_AUTH_ENV: str(auth)}
    api = ResolvedCodexAuth("auto", "api", None, "OPENAI_API_KEY")
    monkeypatch.setattr("raidar.engine.trial.agent_config.resolve_codex_auth", lambda: api)
    assert with_host_auth(codex, Harness.CODEX_CLI).env == {}


def test_run_command_runs_the_selection_into_the_runs_dir(
    tmp_path: Path, fixture_problem: ImportedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(fixture_problem.store.root))
    # API-key auth with a placeholder so the credential preflight never depends on the host.
    monkeypatch.setenv("CODEX_AUTH_MODE", "api")
    monkeypatch.setenv("OPENAI_API_KEY", "x" * 24)
    loaded, _, _ = locked_experiment(
        tmp_path / "exp", fixture_problem.ref, environment={"compose_init": False}
    )
    contexts: list[JobContext] = []

    async def fake_execute(ctx: JobContext) -> list[Any]:
        contexts.append(ctx)
        return []

    seen_names: list[str | None] = []

    def fake_job_target(name: str | None) -> JobTarget:
        seen_names.append(name)
        return JobTarget(SELECTION, None, TargetGuard.for_target(SELECTION.trial, reap=_no_reap))

    monkeypatch.setattr("raidar.engine.job.run.execute", fake_execute)
    monkeypatch.setattr("raidar.cli.execution.job_target", fake_job_target)
    runner = CliRunner()
    arguments = ["run", str(loaded.path), "--arm", "baseline", "--attempt", "1", "--target", "t"]
    jobs = ["--jobs-dir", str(tmp_path / "jobs"), "--job-id", "x"]
    refused = runner.invoke(main, [*arguments, *jobs])
    assert refused.exit_code != 0
    assert "no clean smoke job matches this ab-test experiment" in refused.output
    assert not contexts
    result = runner.invoke(main, [*arguments, *jobs, "--allow-unsmoked"])
    assert result.exit_code == 0, result.output
    (ctx,) = contexts
    assert ctx.admission is not None
    assert ctx.admission["overridden"] is True
    assert seen_names == ["t", "t"]
    assert ctx.target.selection is SELECTION
    assert ctx.job_dir == (tmp_path / "jobs" / loaded.spec.id / "x").resolve()
    assert [t.trial_id for t in ctx.plan.selected_trials()] == ["b01-p0-baseline"]
    assert set(ctx.problems) == {0}
    none = runner.invoke(main, ["run", str(loaded.path), "--arm", "baseline", "--attempt", "7"])
    assert none.exit_code != 0


def test_run_scoped_environment_suffixes_the_compose_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same trial id in two job dirs -> two Compose projects (the pilot's exit-143 collision)."""
    first, second = tmp_path / "exp-a" / "r1" / "trials", tmp_path / "exp-b" / "r1" / "trials"
    assert run_scope(first) == run_scope(first)
    assert run_scope(first) != run_scope(second)
    seen: list[str] = []

    def fake_init(self: object, *args: Any, session_id: str, **kwargs: Any) -> None:
        seen.append(session_id)

    monkeypatch.setattr("harbor.environments.docker.docker.DockerEnvironment.__init__", fake_init)
    for trials in (first, second):
        RunScopedDockerEnvironment(session_id="b00-p0-baseline__env", run_scope=run_scope(trials))
    assert seen == [f"b00-p0-baseline__env-{run_scope(t)}" for t in (first, second)]
    assert len(set(seen)) == 2
