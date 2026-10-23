"""Real-trial fixtures for the liveness spike: agents that are idle, busy-but-quiet, or chatty.

``LivenessProbeAgent`` is a no-LLM Harbor agent whose agent phase lasts ``hold_s``
seconds in one of four shapes (the liveness monitor must call only ``hung`` idle):

- ``hung``: ``sleep`` (no output, no CPU, no network);
- ``busy``: a CPU loop with no output;
- ``chatty``: prints a line every second, otherwise idle;
- ``hung_detached``: ``hung`` plus a ``setsid``-detached sleeper that only the
  uid-based kill can find. After the phase's main exec returns (enforce mode
  kills it) the agent records what the agent uid still runs as root-visible ``ps``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import ClassVar

from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    TaskConfig,
    TrialConfig,
    VerifierConfig,
)

from raidar.execution.cleanup import cleanup_trial_projects
from raidar.execution.environment import RUN_SCOPED_DOCKER_IMPORT_PATH, SCOPE_KWARG, run_scope
from raidar.execution.liveness import install_liveness_monitor
from raidar.targets.config import activate
from tests.component_trial_support import build_task, docker_target

AFTER_PHASE_REPORT = "liveness-after-phase.json"
DETACHED_MARKER = "raidar-liveness-detached"
PASSING_TEST_SH = "#!/bin/bash\nmkdir -p /logs/verifier\necho 1 > /logs/verifier/reward.txt\n"
MODES = ("hung", "busy", "chatty", "hung_detached")


class ProbeOptions(AgentOptions):
    mode: str = "hung"
    hold_s: int = 60


class LivenessProbeAgent(BaseAgent):
    options_model: ClassVar[type[AgentOptions] | None] = ProbeOptions

    @staticmethod
    def name() -> str:
        return "raidar-liveness-probe"

    def version(self) -> str | None:
        return "1"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    def command(self) -> str:
        assert isinstance(self.options, ProbeOptions)
        hold = self.options.hold_s
        match self.options.mode:
            case "hung":
                return f"sleep {hold}"
            case "hung_detached":
                return (
                    f"nohup setsid bash -c 'exec -a {DETACHED_MARKER} sleep 86400' "
                    f">/dev/null 2>&1 < /dev/null & sleep {hold}"
                )
            case "busy":
                return f"timeout {hold} sh -c 'while :; do :; done'"
            case "chatty":
                loop = f"end=$((SECONDS+{hold})); while [ $SECONDS -lt $end ]"
                return f"{loop}; do echo tick; sleep 1; done"
        raise ValueError(f"unknown mode {self.options.mode!r}")

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        result = await environment.exec(self.command(), timeout_sec=self.options.hold_s + 120)
        await asyncio.sleep(2)  # an enforce-mode kill may still be reaping
        alive = await environment.exec(
            f"ps -e -o pid=,uid=,stat=,args= | grep -E '{DETACHED_MARKER}|sleep'"
            " | grep -v grep || true",
            user="root",
        )
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / AFTER_PHASE_REPORT).write_text(
            json.dumps({"main_return_code": result.return_code, "ps": alive.stdout or ""})
        )
        context.metadata = {"mode": self.options.mode}
        if result.return_code != 0:
            # What Harbor's installed agents do (``exec_as_agent``): a failed command raises.
            raise NonZeroAgentExitCodeError(f"Command failed (exit {result.return_code})")


def probe_config(mode: str, hold_s: int) -> AgentConfig:
    return AgentConfig(
        import_path="tests.liveness_support:LivenessProbeAgent",
        kwargs={"mode": mode, "hold_s": hold_s},
    )


async def run_probe_trial(
    root: Path, mode: str, hold_s: int, *, steps: int = 1, verify: bool = False
) -> Path:
    """One trial of ``mode`` (``steps`` steps) with the liveness monitor armed; its trial dir.

    ``verify`` runs Harbor's verifier after every step (a ``test.sh`` that writes reward 1), so
    a step the agent phase ended abnormally still reaches verification as in a real run.
    """
    task_dir, trials_dir = root / f"task-{mode}", root / "trials"
    if trials_dir.exists():
        shutil.rmtree(trials_dir)
    names = {f"checkpoint_{n}": f"checkpoint_{n}" for n in range(1, steps + 1)}
    build_task(task_dir, None, names, agent_timeout_sec=hold_s + 300)
    if verify:
        (task_dir / "tests" / "test.sh").write_text(PASSING_TEST_SH)
    activate(docker_target())
    from harbor.trial.trial import Trial

    config = TrialConfig(
        task=TaskConfig(path=task_dir),
        trial_name=f"liveness-{mode}",
        trials_dir=trials_dir,
        agent=probe_config(mode, hold_s),
        environment=EnvironmentConfig(
            type="docker",
            import_path=RUN_SCOPED_DOCKER_IMPORT_PATH,
            delete=True,
            kwargs={SCOPE_KWARG: run_scope(trials_dir)},
        ),
        verifier=VerifierConfig(disable=not verify),
    )
    try:
        trial = await Trial.create(config)
        install_liveness_monitor(trial)
        await trial.run()
    finally:
        cleanup_trial_projects(root, docker_context=docker_target().docker_context)
    return trials_dir / f"liveness-{mode}"
