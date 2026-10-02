"""Real Harbor trials on the Python pack image for component tests (OrbStack only).

Builds a multi-step Harbor task whose steps carry the compiled per-step
``setup.sh`` (and the first step's overlay), then runs it through Harbor's
Python API with the verifier disabled. Used by the C3 isolation regression
test (no LLM) and the C2/C4 real-trial probe run (codex-cli + gpt-6-luna low).
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, ClassVar

from harbor.agents.base import BaseAgent
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

from raidar.components import CompiledCondition
from raidar.execution.cleanup import cleanup_trial_projects
from raidar.execution.environment import RUN_SCOPED_DOCKER_IMPORT_PATH, SCOPE_KWARG, run_scope
from raidar.packs.python import bind_task_environment, image_tag, load_pack_spec

DOCKER_CONTEXT = "orbstack"
PLANT_MARKER = "raidar-planted-sentinel"
AGENT_HOME = load_pack_spec().image.agent_home
# Every harness-state location the reset owns (step_reset.sh STATE_TARGETS).
PLANT_PATHS = (
    "/tmp/codex-home/sessions/2026/rollout-{token}.jsonl",
    f"{AGENT_HOME}/.codex/memories/{{token}}.md",
    f"{AGENT_HOME}/.agents/skills/leak/{{token}}.md",
    "/logs/agent/sessions/projects/-app/{token}.jsonl",
    f"{AGENT_HOME}/.claude/projects/-app/{{token}}.jsonl",
    f"{AGENT_HOME}/.claude.json",
    "/tmp/raidar-review/{token}.md",
)
PROBE_EXCLUDES = ("proc", "sys", "dev", "usr", "lib", "lib64", "bin", "sbin", "boot", "etc")
STUB_TEST_SH = "#!/bin/bash\n# Verifier disabled in component trials.\nexit 1\n"


def orbstack_available() -> bool:
    try:
        status = subprocess.run(
            ["orbstack-data-status"], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "external_available=True" in status.stdout


def pack_image_present() -> bool:
    try:
        tag = image_tag(load_pack_spec())
        found = subprocess.run(
            ["docker", "--context", DOCKER_CONTEXT, "image", "inspect", tag],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return found.returncode == 0


def build_task(
    task_dir: Path,
    compiled: CompiledCondition | None,
    instructions: dict[str, str],
    *,
    agent_timeout_sec: int = 600,
) -> None:
    """A Harbor ``[[steps]]`` task on the pack image; ``compiled=None`` omits setup.sh."""
    if task_dir.exists():
        shutil.rmtree(task_dir)
    spec = load_pack_spec()
    fields = bind_task_environment(task_dir / "environment", spec)
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "tests" / "test.sh").write_text(STUB_TEST_SH)
    lines = [
        'schema_version = "1.1"',
        "",
        "[task]",
        f'name = "raidar-components/{task_dir.name}"',
        "",
        "[environment]",
        f'docker_image = "{fields["docker_image"]}"',
        'workdir = "/app"',
        "cpus = 2",
        "memory_mb = 4096",
        "",
        "[agent]",
        f"timeout_sec = {agent_timeout_sec}",
        'user = "agent"',
        "",
        "[verifier]",
        'environment_mode = "shared"',
        "",
    ]
    for step, instruction in instructions.items():
        (task_dir / "steps" / step).mkdir(parents=True)
        (task_dir / "steps" / step / "instruction.md").write_text(instruction)
        lines += ["[[steps]]", f'name = "{step}"', ""]
    (task_dir / "task.toml").write_text("\n".join(lines))
    if compiled is not None:
        compiled.write_task_steps(task_dir, list(instructions))


async def run_trial(
    task_dir: Path, trials_dir: Path, agent: AgentConfig, *, trial_name: str
) -> Path:
    """Run one trial in its own Compose project and always remove that project.

    Harbor alone names the project ``<trial_name>__env``, so two test runs on
    one Docker host (same trial name, different ``tmp_path``) recreated each
    other's container. The production run-scoped environment suffixes the
    project with a token of ``trials_dir``, which is unique per test run; the
    ``finally`` removes this run's projects even when the trial raises.
    """
    from harbor.trial.trial import Trial

    if trials_dir.name != "trials":
        raise ValueError(f"trials_dir must be named 'trials' (run scope): {trials_dir}")
    os.environ["DOCKER_CONTEXT"] = DOCKER_CONTEXT
    config = TrialConfig(
        task=TaskConfig(path=task_dir),
        trial_name=trial_name,
        trials_dir=trials_dir,
        agent=agent,
        environment=EnvironmentConfig(
            type="docker",
            import_path=RUN_SCOPED_DOCKER_IMPORT_PATH,
            delete=True,
            kwargs={SCOPE_KWARG: run_scope(trials_dir)},
        ),
        verifier=VerifierConfig(disable=True),
    )
    try:
        trial = await Trial.create(config)
        await trial.run()
    finally:
        cleanup_trial_projects(trials_dir.parent)
    return trials_dir / trial_name


def run_trial_sync(task_dir: Path, trials_dir: Path, agent: AgentConfig, name: str) -> Path:
    return asyncio.run(run_trial(task_dir, trials_dir, agent, trial_name=name))


class IsolationOptions(AgentOptions):
    plant_step: str = "checkpoint_1"


class IsolationProbeAgent(BaseAgent):
    """No-LLM agent for the 0.3 context-isolation check.

    On ``plant_step`` it starts an orphaned ``setsid`` process as the agent
    user and writes a unique token into every harness-state location. On every
    other step it reports whether the planted process is alive and whether any
    earlier token can still be found on the agent-readable filesystem.
    """

    options_model: ClassVar[type[AgentOptions] | None] = IsolationOptions
    tokens: ClassVar[list[str]] = []

    @staticmethod
    def name() -> str:
        return "raidar-isolation-probe"

    def version(self) -> str | None:
        return "1"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    def _write(self, name: str, payload: dict[str, Any]) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / name).write_text(json.dumps(payload, indent=2, sort_keys=True))

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        step = instruction.strip().split()[-1]
        assert isinstance(self.options, IsolationOptions)
        if step == self.options.plant_step:
            await self._plant(environment, step)
        else:
            await self._probe(environment, step)
        context.metadata = {"step": step}

    async def _plant(self, environment: BaseEnvironment, step: str) -> None:
        token = f"RAIDAR-SENTINEL-{uuid.uuid4().hex}"
        type(self).tokens.append(token)
        writes = " && ".join(
            f"mkdir -p {shlex.quote(str(Path(p.format(token=token)).parent))} && "
            f"echo {token} > {shlex.quote(p.format(token=token))}"
            for p in PLANT_PATHS
        )
        await environment.exec(
            f"nohup setsid bash -c 'exec -a {PLANT_MARKER}-{step} sleep 86400' "
            f">/dev/null 2>&1 < /dev/null & {writes}"
        )
        alive = await environment.exec(f"pgrep -af {PLANT_MARKER} || true")
        self._write("raidar-plant.json", {"token": token, "planted": alive.stdout or ""})

    async def _probe(self, environment: BaseEnvironment, step: str) -> None:
        excludes = " ".join(f"--exclude-dir={name}" for name in PROBE_EXCLUDES)
        hits: dict[str, list[str]] = {}
        for token in type(self).tokens:
            found = await environment.exec(
                f"grep -rIl -s {excludes} -F {token} / 2>/dev/null | head -n 20", timeout_sec=300
            )
            hits[token] = [line for line in (found.stdout or "").splitlines() if line.strip()]
        alive = await environment.exec(
            f"ps -e -o pid=,uid=,args= | grep -F {PLANT_MARKER} | grep -v grep || true"
        )
        self._write(
            "raidar-probe.json",
            {
                "step": step,
                "token_hits": hits,
                "planted_alive": [x for x in (alive.stdout or "").splitlines() if x.strip()],
            },
        )
