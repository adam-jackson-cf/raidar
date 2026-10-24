"""Host-side agent liveness monitor (mechanics-v1 design r2 §3.2).

``install_liveness_monitor`` arms one monitor per agent phase (Harbor's
``AGENT_START`` .. ``AGENT_END``) on every trial. ``RAIDAR_LIVENESS`` selects the mode:
``enforce`` (the default: kill the agent uid on the first idle decision),
``observe`` (record only) or ``off``. An agent is *idle* only when all three
signals are quiet for ``RAIDAR_LIVENESS_IDLE_S`` (default 600 s):

1. no output chunk from any exec the agent phase streams (Harbor's log callback);
2. agent-uid CPU: a root ``exec`` every ``RAIDAR_LIVENESS_INTERVAL_S`` (default 15 s)
   reads ``/proc/<pid>/stat`` of every agent-uid process (children included via
   ``cutime``/``cstime``, so short-lived subprocesses are not missed);
3. container network: rx+tx bytes of ``/proc/net/dev`` (loopback excluded).

CPU and network are cumulative counters, so a quiet verdict spans a failed
poll. A failed poll is recorded as ``unmeasured`` and never counts as idle.

Precedence: the harness's and the treatment's own limits win inside their windows;
this is the outer backstop. It terminates and classifies, and never retries. In
``observe`` mode the first idle decision is recorded and sampling goes on; in
``enforce`` mode the agent uid is killed with the reaper's script and sampling
stops. The record lands in ``<trial dir>/raidar-liveness.json`` (host side:
nothing in ``/logs/agent`` that the agent could read or forge); ``raidar health``
audits it (checks ``L1``, ``L2``).
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harbor.trial.hooks import LogEntry, TrialEvent, TrialHookEvent
from harbor.trial.trial import Trial

from raidar.engine.health.reaper import ReapReport, reap_agent_processes

LIVENESS_REPORT = "raidar-liveness.json"
MODE_ENV = "RAIDAR_LIVENESS"
INTERVAL_ENV = "RAIDAR_LIVENESS_INTERVAL_S"
IDLE_ENV = "RAIDAR_LIVENESS_IDLE_S"
CPU_ENV = "RAIDAR_LIVENESS_CPU_TICKS_PER_S"
NET_ENV = "RAIDAR_LIVENESS_NET_BYTES_PER_S"
POLL_TIMEOUT_S = 30
MAX_SAMPLES = 4000
MAX_PROCS_PER_SAMPLE = 20
FLUSH_EVERY_SAMPLES = 20
_ERROR = "@@RAIDAR_ERROR"

# Runs as root inside the trial container. Cumulative counters only.
_SCRIPT = """
uid=$(id -u -- {user} 2>/dev/null) || {{ echo "{error} unknown agent user"; exit 0; }}
echo "@@CLK $(getconf CLK_TCK 2>/dev/null || echo 100)"
echo "@@PROC"
for d in /proc/[0-9]*; do
  [ "$(stat -c %u "$d" 2>/dev/null)" = "$uid" ] && cat "$d/stat" 2>/dev/null
done
echo "@@NET"
cat /proc/net/dev
echo "@@END"
"""


@dataclass(frozen=True)
class LivenessConfig:
    mode: str  # "off" | "observe" | "enforce"
    interval_s: float = 15.0
    idle_s: float = 600.0
    cpu_ticks_per_s: float = 1.0  # agent-uid CPU below 1% of a core is quiet
    net_bytes_per_s: float = 64.0  # keepalives and heartbeats stay below this
    max_samples: int = MAX_SAMPLES

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LivenessConfig:
        env = os.environ if env is None else env
        mode = (env.get(MODE_ENV) or "enforce").strip().lower()
        if mode not in {"off", "observe", "enforce"}:
            raise ValueError(f"{MODE_ENV} must be off, observe or enforce, not {mode!r}")
        defaults = cls(mode)
        return cls(
            mode=mode,
            interval_s=float(env.get(INTERVAL_ENV) or defaults.interval_s),
            idle_s=float(env.get(IDLE_ENV) or defaults.idle_s),
            cpu_ticks_per_s=float(env.get(CPU_ENV) or defaults.cpu_ticks_per_s),
            net_bytes_per_s=float(env.get(NET_ENV) or defaults.net_bytes_per_s),
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "interval_s": self.interval_s,
            "idle_s": self.idle_s,
            "cpu_ticks_per_s": self.cpu_ticks_per_s,
            "net_bytes_per_s": self.net_bytes_per_s,
            "max_samples": self.max_samples,
        }


@dataclass(frozen=True)
class ProcReading:
    clk_tck: int
    procs: dict[int, tuple[str, int]]  # pid -> (comm, own+reaped-children CPU ticks)
    net_bytes: int  # rx + tx over every non-loopback interface


def liveness_script(user: str) -> str:
    return _SCRIPT.format(user=shlex.quote(user), error=_ERROR)


def _parse_stat(line: str) -> tuple[int, str, int] | None:
    """``(pid, comm, ticks)`` from a ``/proc/<pid>/stat`` line; zombies are skipped."""
    head, sep, tail = line.rpartition(")")
    pid_text, _, comm = head.partition(" (")
    fields = tail.split()
    if not sep or not pid_text.strip().isdigit() or len(fields) < 15 or fields[0] == "Z":
        return None
    try:
        ticks = sum(int(fields[i]) for i in (11, 12, 13, 14))  # utime stime cutime cstime
    except ValueError:
        return None
    return int(pid_text), comm, ticks


def _parse_net_dev(lines: list[str]) -> int | None:
    total, seen = 0, False
    for line in lines:
        name, sep, counters = line.partition(":")
        values = counters.split()
        if not sep or name.strip() == "lo" or len(values) < 9:
            continue
        try:
            total += int(values[0]) + int(values[8])
        except ValueError:
            return None
        seen = True
    return total if seen else None


def _sections(output: str) -> dict[str, list[str]] | None:
    """The poll output split at its ``@@`` markers; ``None`` when the script reported an error."""
    sections: dict[str, list[str]] = {"@@CLK": [], "@@PROC": [], "@@NET": [], "@@END": []}
    current: list[str] | None = None
    for line in output.splitlines():
        if line.startswith(_ERROR):
            return None
        marker = line.split(" ", 1)[0]
        if marker in sections:
            current = sections[marker]
            if marker == "@@CLK":
                current.append(line[len(marker) :].strip())
        elif current is not None and line.strip():
            current.append(line)
    return sections


def parse_poll_output(output: str) -> ProcReading | None:
    """The reading, or ``None`` (unmeasured) when the poll output is missing or incomplete."""
    sections = _sections(output)
    if sections is None or "@@END" not in output:
        return None
    clock = sections["@@CLK"]
    net = _parse_net_dev(sections["@@NET"])
    if not clock or not clock[0].isdigit() or net is None:
        return None
    parsed = (_parse_stat(line) for line in sections["@@PROC"])
    procs = {item[0]: (item[1], item[2]) for item in parsed if item is not None}
    return ProcReading(int(clock[0]), procs, net)


@dataclass
class OutputTally:
    """Output chunks the phase's execs streamed, and how many the last sample accounted for."""

    last: float
    chunks: int = 0
    seen: int = 0


@dataclass
class Verdict:
    """The phase's idle decision: ``active`` until the first idle window, then final."""

    decision: str = "active"
    reason: str = "no idle window reached"
    first_idle_at_s: float | None = None
    kill: dict[str, Any] | None = None
    idle_for_s: float | None = None


@dataclass(frozen=True)
class AgentHost:
    """Where the agent runs: its container environment and the uid whose processes count."""

    environment: Any
    user: str | None


class PhaseMonitor:
    """Liveness state of one agent phase. ``sample`` is the unit; ``run`` loops it."""

    def __init__(
        self,
        config: LivenessConfig,
        environment: Any,
        user: str | None,
        *,
        step: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.step = step
        self._host = AgentHost(environment, user)
        self._clock = clock
        self._started = clock()
        self._output = OutputTally(self._started)
        self._baseline: tuple[float, ProcReading] | None = None
        self._quiet_since: float | None = None
        self.samples: deque[dict[str, Any]] = deque(maxlen=config.max_samples)
        self.sample_count = 0
        self.verdict = Verdict()
        self.error: str | None = None  # why the sampling loop died, if it did

    @property
    def stopped(self) -> bool:
        return self.verdict.decision in {"idle_killed", "kill_failed"}

    def note_output(self) -> None:
        self._output.last = self._clock()
        self._output.chunks += 1

    async def _poll(self) -> tuple[ProcReading | None, str | None]:
        if self._host.user is None:
            return None, "the task sets no agent user"
        try:
            result = await self._host.environment.exec(
                liveness_script(self._host.user), user="root", timeout_sec=POLL_TIMEOUT_S
            )
        except Exception as error:  # an unmeasured sample, never a trial failure
            return None, f"poll exec failed: {type(error).__name__}: {error}"
        output = f"{result.stdout or ''}\n{result.stderr or ''}"
        reading = parse_poll_output(output)
        return (reading, None) if reading else (None, f"poll output unusable: {output[-200:]!r}")

    async def sample(self) -> dict[str, Any]:
        now = self._clock()
        t = round(now - self._started, 3)
        reading, error = await self._poll()
        record: dict[str, Any] = {"t": t, "measured": reading is not None}
        self.sample_count += 1
        if reading is None:
            record["unmeasured"] = error
        elif self._baseline is None:
            record["baseline"] = True
            self._baseline = (now, reading)
        else:
            record.update(self._evaluate(now, reading))
        record["output_age_s"] = round(now - self._output.last, 3)
        self.samples.append(record)
        if reading is not None:
            await self._maybe_decide(now, record)
        return record

    def _evaluate(self, now: float, reading: ProcReading) -> dict[str, Any]:
        assert self._baseline is not None
        prev_t, prev = self._baseline
        span = max(now - prev_t, 1e-9)
        deltas = {
            pid: max(0, ticks - prev.procs.get(pid, ("", 0))[1])
            for pid, (_, ticks) in reading.procs.items()
        }
        cpu_ticks = sum(deltas.values())
        net_bytes = max(0, reading.net_bytes - prev.net_bytes)
        output_chunks = self._output.chunks - self._output.seen
        quiet = (
            output_chunks == 0
            and cpu_ticks / span < self.config.cpu_ticks_per_s * reading.clk_tck / 100
            and net_bytes / span < self.config.net_bytes_per_s
        )
        self._baseline = (now, reading)
        self._output.seen = self._output.chunks
        if not quiet:
            self._quiet_since = None
        elif self._quiet_since is None:
            self._quiet_since = prev_t
        top = sorted(deltas.items(), key=lambda item: -item[1])[:MAX_PROCS_PER_SAMPLE]
        return {
            "quiet": quiet,
            "output_chunks": output_chunks,
            "cpu_ticks": cpu_ticks,
            "net_bytes": net_bytes,
            "procs": [
                {"pid": pid, "comm": reading.procs[pid][0], "ticks": ticks, "delta": delta}
                for pid, delta in top
                for ticks in [reading.procs[pid][1]]
            ],
        }

    async def _maybe_decide(self, now: float, record: dict[str, Any]) -> None:
        if self._quiet_since is None or now - self._quiet_since < self.config.idle_s:
            return
        idle_for = round(now - self._quiet_since, 3)
        record["idle_for_s"] = idle_for
        verdict = self.verdict
        if verdict.first_idle_at_s is not None:
            return  # observe mode: the first decision stands; sampling continues
        verdict.first_idle_at_s = round(now - self._started, 3)
        verdict.idle_for_s = idle_for
        verdict.reason = (
            f"no output, agent CPU or network for {idle_for}s (threshold {self.config.idle_s}s)"
        )
        if self.config.mode != "enforce":
            verdict.decision = "idle_observed"
            return
        report = await self._reap()
        verdict.kill = report.as_json()
        verdict.decision = "kill_failed" if report.error or report.residual else "idle_killed"

    async def _reap(self) -> ReapReport:
        """Kill the agent uid; a cancel (the phase ended, perhaps because of the kill) never
        abandons the kill's report, so the decision is always recorded."""
        reaping = asyncio.ensure_future(
            reap_agent_processes(self._host.environment, self._host.user)
        )
        try:
            return await asyncio.shield(reaping)
        except asyncio.CancelledError:
            report = await reaping
            self.verdict.kill = report.as_json()
            self.verdict.decision = (
                "kill_failed" if report.error or report.residual else "idle_killed"
            )
            raise

    def as_json(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "decision": self.verdict.decision,
            "reason": self.verdict.reason,
            "first_idle_at_s": self.verdict.first_idle_at_s,
            "duration_s": round(self._clock() - self._started, 3),
            "idle_for_s": self.verdict.idle_for_s,
            "kill": self.verdict.kill,
            "error": self.error,
            "sample_count": self.sample_count,
            "samples_dropped": max(0, self.sample_count - len(self.samples)),
            "unmeasured_samples": sum(1 for s in self.samples if not s["measured"]),
            "samples": list(self.samples),
        }

    async def run(self, flush: Callable[[], None]) -> None:
        try:
            while not self.stopped:
                await self.sample()
                if (
                    self.sample_count % FLUSH_EVERY_SAMPLES == 0
                    or self.verdict.decision != "active"
                ):
                    flush()
                if self.stopped:
                    break
                await asyncio.sleep(self.config.interval_s)
        except Exception as error:  # noqa: BLE001 - recorded, never raised over the agent outcome
            self.error = f"{type(error).__name__}: {error}"


class TrialLiveness:
    """Per-trial hook state: the active phase monitor plus every finished phase."""

    def __init__(self, trial: Trial, config: LivenessConfig) -> None:
        self.trial = trial
        self.config = config
        self.active: PhaseMonitor | None = None
        self.task: asyncio.Task[None] | None = None
        self.phases: list[dict[str, Any]] = []

    def write(self) -> None:
        phases = [*self.phases, *([self.active.as_json()] if self.active else [])]
        path = Path(self.trial.paths.trial_dir) / LIVENESS_REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "kind": "raidar-liveness",
                    "config": self.config.as_json(),
                    "phases": phases,
                },
                indent=1,
                sort_keys=True,
            )
        )

    async def on_output(self, entry: LogEntry) -> None:
        if self.active is not None and entry.phase == "agent":
            self.active.note_output()

    async def on_start(self, _event: TrialHookEvent) -> None:
        user = self.trial.task.config.agent.user
        self.active = PhaseMonitor(
            self.config,
            self.trial.agent_environment,
            None if user is None else str(user),
        )
        self.task = asyncio.create_task(self.active.run(self.write))

    async def on_end(self, event: TrialHookEvent) -> None:
        monitor, task = self.active, self.task
        self.active = self.task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if monitor is not None:
            monitor.step = _step_name(event)  # the step result exists by AGENT_END
            self.phases.append(monitor.as_json())
        self.write()


def _step_name(event: TrialHookEvent) -> str | None:
    try:
        steps = event.result.step_results if event.result else []
        return str(steps[-1].step_name) if steps else None
    except Exception:  # noqa: BLE001 - a label only
        return None


def install_liveness_monitor(trial: Trial, config: LivenessConfig | None = None) -> None:
    """Arm the monitor on a trial; a no-op only when ``RAIDAR_LIVENESS`` is ``off``."""
    config = LivenessConfig.from_env() if config is None else config
    if config.mode == "off":
        return
    state = TrialLiveness(trial, config)
    trial.add_log_callback(state.on_output)
    trial.add_hook(TrialEvent.AGENT_START, state.on_start)
    trial.add_hook(TrialEvent.AGENT_END, state.on_end)


def idle_killed_steps(trial_dir: Path) -> frozenset[str]:
    """Steps whose agent phase ended in an enforce-mode idle kill (``idle_killed``)."""
    try:
        phases = json.loads((trial_dir / LIVENESS_REPORT).read_text()).get("phases", [])
    except (OSError, ValueError, AttributeError):
        return frozenset()
    return frozenset(
        str(p["step"])
        for p in phases
        if isinstance(p, dict) and p.get("decision") == "idle_killed" and p.get("step")
    )
