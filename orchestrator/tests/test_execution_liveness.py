"""Agent liveness monitor (spike): idle only when output, agent CPU and network are all quiet."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from harbor.trial.hooks import TrialEvent

from raidar.execution.liveness import (
    LIVENESS_REPORT,
    LivenessConfig,
    PhaseMonitor,
    install_liveness_monitor,
    liveness_script,
    parse_poll_output,
)
from raidar.execution.reaper import _KILLED, _RESIDUAL

NET_HEADER = (
    "Inter-|   Receive\n face |bytes packets errs drop fifo frame compressed multicast|bytes\n"
)


def stat_line(pid: int, comm: str, ticks: int, state: str = "S") -> str:
    # fields after ")": state ppid pgrp session tty tpgid flags minflt cminflt majflt cmajflt
    #                    utime stime cutime cstime
    return f"{pid} ({comm}) {state} 1 1 1 0 -1 0 0 0 0 0 {ticks} 0 0 0 20 0 1 0 5 0 0"


def net_line(name: str, rx: int, tx: int) -> str:
    return f"  {name}: {rx} 1 0 0 0 0 0 0 {tx} 1 0 0 0 0 0 0"


def poll_output(procs: list[str], rx: int = 1000, tx: int = 1000, lo: int = 10**9) -> str:
    return "\n".join(
        ["@@CLK 100", "@@PROC", *procs, "@@NET", NET_HEADER.rstrip(), net_line("lo", lo, lo)]
        + [net_line("eth0", rx, tx), "@@END", ""]
    )


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class ScriptedEnv:
    """Answers every exec with the next scripted stdout (or raises it)."""

    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies
        self.calls: list[dict[str, Any]] = []

    async def exec(self, command: str, **kwargs: Any) -> Any:
        self.calls.append({"command": command, **kwargs})
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(stdout=reply, stderr="", return_code=0)


def monitor(
    env: ScriptedEnv, clock: Clock, mode: str = "observe", idle_s: float = 60.0
) -> PhaseMonitor:
    config = LivenessConfig(mode, interval_s=15, idle_s=idle_s)
    return PhaseMonitor(config, env, "agent", clock=clock)


def run_samples(m: PhaseMonitor, clock: Clock, count: int, step_s: float = 15.0) -> None:
    async def go() -> None:
        for _ in range(count):
            await m.sample()
            clock.now += step_s

    asyncio.run(go())


def test_poll_parsing_sums_agent_ticks_and_skips_loopback_and_zombies() -> None:
    text = poll_output(
        [stat_line(5, "my (weird) comm", 7), stat_line(6, "gone", 99, "Z")], rx=300, tx=40
    )
    reading = parse_poll_output(text)
    assert reading is not None
    assert reading.procs == {5: ("my (weird) comm", 7)}
    assert reading.net_bytes == 340
    assert reading.clk_tck == 100


@pytest.mark.parametrize(
    "text", ["", "@@RAIDAR_ERROR unknown agent user x", "@@CLK 100\n@@PROC\n@@NET\n"]
)
def test_unusable_poll_output_is_unmeasured(text: str) -> None:
    assert parse_poll_output(text) is None


def test_script_quotes_the_user() -> None:
    assert "'a b'" in liveness_script("a b")


def test_all_quiet_for_the_idle_window_is_idle_and_observe_mode_keeps_sampling() -> None:
    clock, env = Clock(), ScriptedEnv([poll_output([stat_line(5, "sleep", 3)])])
    m = monitor(env, clock)
    run_samples(m, clock, 3)
    assert m.verdict.decision == "active"
    run_samples(m, clock, 3)
    assert m.verdict.decision == "idle_observed"
    assert m.verdict.first_idle_at_s == 60.0
    first = m.verdict.first_idle_at_s
    run_samples(m, clock, 3)
    assert m.verdict.first_idle_at_s == first
    assert m.sample_count == 9
    assert all(call["user"] == "root" for call in env.calls)


def test_agent_cpu_alone_keeps_the_agent_active() -> None:
    clock = Clock()
    env = ScriptedEnv([poll_output([stat_line(5, "python", 300 * i)]) for i in range(20)])
    m = monitor(env, clock)
    run_samples(m, clock, 12)
    assert m.verdict.decision == "active"
    assert m.samples[-1]["quiet"] is False
    assert m.samples[-1]["cpu_ticks"] == 300


def test_network_alone_keeps_the_agent_active() -> None:
    clock = Clock()
    env = ScriptedEnv(
        [poll_output([stat_line(5, "node", 3)], rx=5000 * i, tx=0) for i in range(20)]
    )
    m = monitor(env, clock)
    run_samples(m, clock, 12)
    assert m.verdict.decision == "active"


def test_output_alone_keeps_the_agent_active() -> None:
    clock, env = Clock(), ScriptedEnv([poll_output([stat_line(5, "sleep", 3)])])
    m = monitor(env, clock)

    async def go() -> None:
        for _ in range(12):
            m.note_output()
            await m.sample()
            clock.now += 15

    asyncio.run(go())
    assert m.verdict.decision == "active"


def test_a_burst_of_cpu_resets_the_idle_window() -> None:
    clock = Clock()
    ticks = [3] * 4 + [900] + [900] * 3
    env = ScriptedEnv([poll_output([stat_line(5, "x", t)]) for t in ticks])
    m = monitor(env, clock)
    run_samples(m, clock, len(ticks))
    assert m.verdict.decision == "active"  # quiet only since the burst: 45 s < 60 s


def test_a_failed_poll_is_unmeasured_and_never_idle() -> None:
    clock = Clock()
    env = ScriptedEnv([RuntimeError("container stopped")])
    m = monitor(env, clock)
    run_samples(m, clock, 20)
    assert m.verdict.decision == "active"
    assert m.verdict.first_idle_at_s is None
    assert all(not s["measured"] for s in m.samples)
    assert "container stopped" in m.samples[0]["unmeasured"]
    assert m.as_json()["unmeasured_samples"] == 20


def test_counters_bridge_a_failed_poll_but_the_gap_alone_never_decides() -> None:
    clock = Clock()
    quiet = poll_output([stat_line(5, "sleep", 3)])
    env = ScriptedEnv([quiet, quiet, RuntimeError("blip"), RuntimeError("blip"), quiet])
    m = monitor(env, clock, idle_s=45)
    run_samples(m, clock, 4)
    assert m.verdict.decision == "active"  # last measured sample is 15 s into the quiet window
    run_samples(m, clock, 1)
    assert m.verdict.decision == "idle_observed"  # cumulative counters prove the gap was quiet too


def test_enforce_mode_kills_the_agent_uid_with_the_reaper_script() -> None:
    clock = Clock()
    quiet = poll_output([stat_line(5, "sleep", 3)])
    kill_output = f"{_KILLED}\n5 1000 S sleep\n{_RESIDUAL}\n"
    env = ScriptedEnv([quiet, quiet, quiet, quiet, kill_output])
    m = monitor(env, clock, mode="enforce", idle_s=45)
    run_samples(m, clock, 4)
    assert m.verdict.decision == "idle_killed"
    assert m.stopped
    assert m.verdict.kill is not None
    assert m.verdict.kill["residual"] == []
    assert "kill -KILL" in env.calls[-1]["command"]


def test_enforce_mode_reports_a_failed_kill() -> None:
    clock = Clock()
    quiet = poll_output([stat_line(5, "sleep", 3)])
    survivors = f"{_KILLED}\n{_RESIDUAL}\n5 1000 S sleep\n"
    m = monitor(ScriptedEnv([quiet] * 4 + [survivors]), clock, mode="enforce", idle_s=45)
    run_samples(m, clock, 4)
    assert m.verdict.decision == "kill_failed"


def test_sample_history_is_bounded() -> None:
    clock = Clock()
    config = LivenessConfig("observe", idle_s=10**9, max_samples=5)
    m = PhaseMonitor(config, ScriptedEnv([poll_output([])]), "agent", clock=clock)
    run_samples(m, clock, 9)
    assert len(m.samples) == 5
    assert m.as_json()["samples_dropped"] == 4


def test_config_from_env_observes_by_default_and_enforce_is_opt_in() -> None:
    default = LivenessConfig.from_env({})
    assert (default.mode, default.idle_s, default.interval_s) == ("observe", 600.0, 15.0)
    assert LivenessConfig.from_env({"RAIDAR_LIVENESS": "off"}).mode == "off"
    config = LivenessConfig.from_env({"RAIDAR_LIVENESS": "enforce", "RAIDAR_LIVENESS_IDLE_S": "20"})
    assert (config.mode, config.idle_s, config.interval_s) == ("enforce", 20.0, 15.0)
    with pytest.raises(ValueError, match="observe or enforce"):
        LivenessConfig.from_env({"RAIDAR_LIVENESS": "maybe"})


def fake_trial(tmp_path: Path, env: ScriptedEnv) -> tuple[Any, dict[Any, list[Any]], list[Any]]:
    hooks: dict[Any, list[Any]] = {}
    callbacks: list[Any] = []
    trial = SimpleNamespace(
        task=SimpleNamespace(config=SimpleNamespace(agent=SimpleNamespace(user="agent"))),
        agent_environment=env,
        paths=SimpleNamespace(trial_dir=tmp_path / "trial"),
        add_hook=lambda event, hook: hooks.setdefault(event, []).append(hook),
        add_log_callback=callbacks.append,
    )
    return trial, hooks, callbacks


def test_off_installs_nothing(tmp_path: Path) -> None:
    trial, hooks, callbacks = fake_trial(tmp_path, ScriptedEnv([""]))
    install_liveness_monitor(cast(Any, trial), LivenessConfig("off"))
    assert hooks == {}
    assert callbacks == []


def test_hooks_arm_the_phase_and_write_the_host_side_report(tmp_path: Path) -> None:
    env = ScriptedEnv([poll_output([stat_line(5, "sleep", 3)])])
    trial, hooks, callbacks = fake_trial(tmp_path, env)
    config = LivenessConfig("observe", interval_s=0.01, idle_s=0.03)
    install_liveness_monitor(cast(Any, trial), config)
    event = SimpleNamespace(
        result=SimpleNamespace(step_results=[SimpleNamespace(step_name="checkpoint_1")])
    )

    async def phase() -> None:
        await hooks[TrialEvent.AGENT_START][0](event)
        await callbacks[0](SimpleNamespace(phase="agent_setup"))  # outside the phase: ignored
        await asyncio.sleep(0.3)
        await callbacks[0](SimpleNamespace(phase="agent"))
        await hooks[TrialEvent.AGENT_END][0](event)

    asyncio.run(phase())
    report = json.loads((tmp_path / "trial" / LIVENESS_REPORT).read_text())
    (recorded,) = report["phases"]
    assert recorded["step"] == "checkpoint_1"
    assert recorded["decision"] == "idle_observed"
    assert report["config"]["mode"] == "observe"
    assert recorded["samples"][0]["baseline"]
