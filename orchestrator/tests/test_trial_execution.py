"""Trial decisions: concurrency, host credentials, and pre-agent protection."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from harbor.environments.base import ExecResult
from harbor.models.trial.config import AgentConfig, TrialConfig
from harbor.trial.hooks import TrialEvent

from raidar.engine.job import provenance
from raidar.engine.job.provenance import execution_source_digest
from raidar.engine.trial import driver
from raidar.engine.trial.agent_config import reported_harness_pin
from raidar.engine.trial.driver import TrialRun, run_harbor_trial, run_in_order
from raidar.engine.trial.environment import RunScopedDockerEnvironment, run_scope
from raidar.engine.trial.host_auth import CODEX_AUTH_ENV, with_host_auth
from raidar.grading import guard, leak
from raidar.subject.agent.config import Harness
from raidar.subject.workflow.runner import WorkflowRunner


@pytest.mark.parametrize(
    ("leaked", "exception_type"),
    [(False, None), (False, "AgentTimeoutError"), (True, None)],
)
async def test_hidden_tests_stop_the_agent_before_it_can_read_them(
    monkeypatch: pytest.MonkeyPatch, leaked: bool, exception_type: str | None
) -> None:
    reports: list[Any] = []
    bundle = SimpleNamespace(
        resolve_step=lambda _step: "checkpoint_1",
        hidden_file_hashes=lambda: {"a" * 64: "test_private.py"},
    )
    monkeypatch.setattr(guard, "load_bundle", lambda _path: bundle)
    monkeypatch.setattr(guard, "write_pre_agent_leak", lambda _path, report: reports.append(report))
    monkeypatch.setattr(leak.time, "perf_counter", lambda: 0.0)
    for installer in (
        "install_agent_reaper",
        "install_trial_provisioning",
        "install_tool_tree_watch",
        "install_liveness_monitor",
    ):
        monkeypatch.setattr(driver, installer, lambda *_args: None)

    class ProtectedTrial:
        paths = SimpleNamespace(trial_dir=Path("/trial"))
        agent_started = False

        def __init__(self) -> None:
            self.hooks: dict[TrialEvent, list[Any]] = {}
            self.agent_environment = self

        def add_hook(self, event: TrialEvent, hook: Any) -> None:
            self.hooks.setdefault(event, []).append(hook)

        async def exec(self, _command: str, **_kwargs: Any) -> ExecResult:
            planted = "TESTS\t/tests/private.py\n" if leaked else ""
            return ExecResult(return_code=0, stdout=planted + "RAIDAR_LEAK_SCAN_DONE\n")

        async def run(self) -> Any:
            event = SimpleNamespace(
                result=SimpleNamespace(step_results=[SimpleNamespace(step_name="checkpoint_1")])
            )
            for hook in self.hooks.get(TrialEvent.AGENT_START, []):
                await hook(event)
            self.agent_started = True
            info = SimpleNamespace(exception_type=exception_type) if exception_type else None
            return SimpleNamespace(exception_info=info)

    trial = ProtectedTrial()

    async def create(_config: TrialConfig) -> ProtectedTrial:
        return trial

    monkeypatch.setattr(driver.Trial, "create", create)
    if leaked:
        with pytest.raises(leak.LeakScanError):
            await run_harbor_trial(TrialConfig.model_construct(), Path("/bundle"))
    else:
        outcome = await run_harbor_trial(TrialConfig.model_construct(), Path("/bundle"))
        assert outcome == TrialRun(Path("/trial"), exception_type)
    assert trial.agent_started is not leaked
    (report,) = reports
    assert report.clean is not leaked
    assert report.tests_dir_entries == (("/tests/private.py",) if leaked else ())


def test_codex_gets_the_host_session_file_path_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text('{"tokens": {"access_token": "x"}}')
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_AUTH_MODE", "auto")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    codex = AgentConfig(name="codex")
    assert with_host_auth(codex, Harness.CODEX_CLI).env == {CODEX_AUTH_ENV: str(auth)}
    assert with_host_auth(AgentConfig(name="oracle"), Harness.CODEX_CLI).env == {}
    wrapper = AgentConfig(
        import_path=WorkflowRunner.import_path(),
        kwargs={"inner_agent": {"name": "codex", "kwargs": {"version": "0.156.1"}}},
    )
    # A workflow wrapper is held to its wrapped harness's pin; a no-LLM override has none.
    assert reported_harness_pin(wrapper) == "0.156.1"
    assert reported_harness_pin(AgentConfig(name="codex", kwargs={"version": "0.156.1"})) == (
        "0.156.1"
    )
    assert reported_harness_pin(AgentConfig(name="oracle")) is None
    # The runner passes its env to every inner turn: without
    # the session file its inner codex called the API with an empty key (401, pilot).
    assert with_host_auth(wrapper, Harness.CODEX_CLI).env == {CODEX_AUTH_ENV: str(auth)}
    monkeypatch.setenv("CODEX_AUTH_MODE", "api")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert with_host_auth(codex, Harness.CODEX_CLI).env == {}


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


@pytest.mark.parametrize("owner", ["engine/health", "grading", "subject/agent", "contract"])
def test_execution_provenance_tracks_behavior_and_source_paths_but_not_documentation(
    tmp_path: Path, owner: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provenance, "contract_sources", dict)
    root = tmp_path / "raidar"
    source = root / owner / "operation.py"
    source.parent.mkdir(parents=True)
    source.write_text('"""Operation."""\nLIMIT = 1\n')
    initial = execution_source_digest(root)
    source.write_text('"""Reworded operation."""\n# Clarification.\nLIMIT = 1\n')
    assert execution_source_digest(root) == initial
    source.write_text("LIMIT = 2\n")
    changed = execution_source_digest(root)
    assert changed != initial
    source.rename(source.with_name("renamed.py"))
    assert execution_source_digest(root) != changed


def test_execution_provenance_refuses_an_empty_source_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provenance, "contract_sources", dict)
    with pytest.raises(ValueError, match="no Raidar Python source"):
        execution_source_digest(tmp_path)


async def _never(job: int) -> int:
    raise AssertionError("no job may start")


async def _run_gated(jobs: list[int], concurrency: int) -> tuple[list[int], list[int], int]:
    """Run ``jobs`` with each worker held on its own gate; release the newest running one first,
    so completion order differs from start order. Synchronised on events, never on time."""
    gates = {job: asyncio.Event() for job in jobs}
    progress = asyncio.Event()
    started: list[int] = []
    running = 0
    peak = 0

    async def worker(job: int) -> int:
        nonlocal running, peak
        started.append(job)
        running += 1
        peak = max(peak, running)
        progress.set()
        await gates[job].wait()
        running -= 1
        return job * 10

    async def conductor() -> None:
        released = 0
        while released < len(jobs):
            await progress.wait()
            progress.clear()
            live = [job for job in started if not gates[job].is_set()]
            if len(live) == concurrency or released + len(live) == len(jobs):
                gates[live[-1]].set()
                released += 1
                progress.set()

    gate_keeper = asyncio.create_task(conductor())
    results = await run_in_order(jobs, worker, concurrency=concurrency)
    await gate_keeper
    return results, started, peak


def test_trials_start_in_plan_order_and_never_exceed_concurrency() -> None:
    jobs = list(range(12))
    results, started, peak = asyncio.run(_run_gated(jobs, 3))
    assert results == [job * 10 for job in jobs]
    assert started == jobs
    assert peak == 3
    serial, _, serial_peak = asyncio.run(_run_gated([1, 2], 1))
    assert serial == [10, 20]
    assert serial_peak == 1
    with pytest.raises(ValueError, match="concurrency"):
        asyncio.run(run_in_order([1], _never, concurrency=0))


def test_a_raising_worker_starts_nothing_more_and_leaves_nothing_running() -> None:
    started: list[int] = []
    finished: list[int] = []
    cancelled: list[int] = []

    async def worker(job: int) -> int:
        started.append(job)
        if job == 1:
            raise RuntimeError("Trial.create: docker unreachable")
        try:
            await asyncio.Event().wait()  # only cancellation ends this sibling
        except asyncio.CancelledError:
            cancelled.append(job)
            raise
        finished.append(job)
        return job

    with pytest.raises(RuntimeError, match="docker unreachable"):
        asyncio.run(run_in_order([0, 1, 2, 3, 4, 5], worker, concurrency=2))
    assert started == [0, 1]  # stopped starting jobs once the failure was seen
    assert cancelled == [0]  # the sibling was cancelled, not orphaned
    assert finished == []


def test_a_refused_admission_starts_nothing_more_but_lets_running_trials_finish() -> None:
    admitted: list[int] = []
    started: list[int] = []
    finished: list[int] = []
    proceed = asyncio.Event()

    async def admit(job: int) -> None:
        admitted.append(job)
        if job == 2:
            proceed.set()  # the running trials complete on their own
            raise RuntimeError("target lost")

    async def worker(job: int) -> int:
        started.append(job)
        await proceed.wait()
        finished.append(job)
        return job

    with pytest.raises(RuntimeError, match="target lost"):
        asyncio.run(run_in_order([0, 1, 2, 3], worker, concurrency=3, admit=admit))
    assert admitted == [0, 1, 2]
    assert started == [0, 1]
    assert finished == [0, 1]


def test_admission_runs_before_each_trial_starts() -> None:
    events: list[str] = []

    async def admit(job: int) -> None:
        events.append(f"admit{job}")

    async def worker(job: int) -> int:
        events.append(f"run{job}")
        return job

    results = asyncio.run(run_in_order([0, 1], worker, concurrency=1, admit=admit))
    assert results == [0, 1]
    assert events == ["admit0", "run0", "admit1", "run1"]
