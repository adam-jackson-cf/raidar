"""Spike agents for the A' checks (throwaway; plan task 0.3).

Both agents share the same probe/plant instrumentation so that the context
isolation evidence is identical for no-LLM and real-LLM trials:

* at step start (after Harbor ran the per-step ``setup.sh``) a probe runs as the
  agent user and records whether the step-1 planted process is alive and
  whether any step-1 sentinel/transcript token is findable anywhere on the
  agent-writable filesystem;
* at the end of the plant step a background process is started as the agent
  user and harness-state sentinels are written into CODEX_HOME and
  CLAUDE_CONFIG_DIR locations.

``SpikeCodexAgent`` runs the real ``codex exec`` CLI. It deliberately keeps
CODEX_HOME at ``$HOME/.codex`` (outside Harbor's /logs/agent management) and
does **not** pass ``--ephemeral``, so real Codex session state persists unless
the per-step reset removes it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import uuid
from pathlib import Path
from typing import ClassVar

from harbor.agents.base import BaseAgent
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

PLANT_MARKER = "raidar-planted-sentinel"
AGENT_HOME = "/tmp/agent_home"
CODEX_HOME = f"{AGENT_HOME}/.codex"
CLAUDE_CONFIG_DIR = "/logs/agent/sessions"
PROBE_EXCLUDES = (
    "proc",
    "sys",
    "dev",
    "usr",
    "lib",
    "lib64",
    "bin",
    "sbin",
    "boot",
    "etc",
    "run",
    "var/lib",
    "var/cache",
)


def _step_index(oracle_dir: str) -> dict[str, str]:
    index_path = Path(oracle_dir) / "instruction_index.json"
    return json.loads(index_path.read_text(encoding="utf-8"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SpikeAgentOptions(AgentOptions):
    oracle_dir: str
    plant_step: str | None = "checkpoint_1"


class ReferenceAgentOptions(SpikeAgentOptions):
    # Steps on which the reference agent does nothing (forces an oracle failure).
    skip_steps: list[str] = []
    # Negative control only: copy one hidden test file into the env on this step.
    leak_step: str | None = None


class CodexAgentOptions(SpikeAgentOptions):
    reasoning_effort: str = "low"
    auth_json_env: str = "AGENTIC_EVAL_SECRET_FILE_CODEX_AUTH_JSON"


class _ProbeMixin(BaseAgent):
    """Probe + plant instrumentation shared by both spike agents."""

    _tokens: list[str]

    def _init_probe_state(self) -> None:
        self._tokens = []
        self._index = _step_index(self.options.oracle_dir)  # type: ignore[attr-defined]

    def _resolve_step(self, instruction: str) -> str:
        step = self._index.get(_sha(instruction))
        if step is None:
            raise RuntimeError("instruction does not match any known step")
        return step

    async def _write_json(self, environment: BaseEnvironment, path: str, payload: dict) -> None:
        """Write evidence host-side into the agent logs dir (never into the env)."""
        del environment
        target = Path(self.logs_dir) / Path(path).name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    async def _probe(self, environment: BaseEnvironment, step: str) -> dict:
        excludes = " ".join(f"--exclude-dir={shlex.quote(d)}" for d in PROBE_EXCLUDES)
        findings: dict[str, list[str]] = {}
        for token in self._tokens:
            # Run as the agent user: this is what the agent itself could find.
            result = await environment.exec(
                f"grep -rIl -s {excludes} -F {shlex.quote(token)} / 2>/dev/null | head -n 20",
                timeout_sec=300,
            )
            hits = [line for line in (result.stdout or "").splitlines() if line.strip()]
            # /logs/agent/raidar-* are this probe's own reports (host-side evidence).
            findings[token] = [h for h in hits if not h.startswith("/logs/agent/raidar-")]
        procs = await environment.exec(
            f"ps -e -o pid=,uid=,args= | grep -F {PLANT_MARKER} | grep -v grep || true"
        )
        state = await environment.exec(
            f"for d in {CODEX_HOME} {AGENT_HOME}/.claude {CLAUDE_CONFIG_DIR}; do "
            'if [ -e "$d" ]; then find "$d" -mindepth 1 | head -n 50 | sed "s|^|$d: |"; fi; done'
        )
        whoami = await environment.exec("id -u")
        report = {
            "kind": "raidar-step-probe",
            "step": step,
            "probe_uid": (whoami.stdout or "").strip(),
            "searched_tokens": list(self._tokens),
            "token_hits": findings,
            "planted_process_alive": [
                line.strip() for line in (procs.stdout or "").splitlines() if line.strip()
            ],
            "harness_state_entries": [
                line for line in (state.stdout or "").splitlines() if line.strip()
            ],
        }
        await self._write_json(environment, "/logs/agent/raidar-probe.json", report)
        return report

    async def _plant(self, environment: BaseEnvironment, step: str) -> dict:
        token = f"RAIDAR-SENTINEL-{step}-{uuid.uuid4().hex}"
        self._tokens.append(token)
        cmd = (
            f"nohup setsid bash -c 'exec -a {PLANT_MARKER}-{step} sleep 86400' "
            ">/dev/null 2>&1 < /dev/null & "
            f"mkdir -p {CODEX_HOME}/sessions/raidar {CODEX_HOME}/memories "
            f"{CLAUDE_CONFIG_DIR}/projects/-app {AGENT_HOME}/.claude/projects/-app && "
            f"echo {token} > {CODEX_HOME}/sessions/raidar/rollout-{token}.jsonl && "
            f"echo {token} >> {CODEX_HOME}/history.jsonl && "
            f"echo {token} > {CODEX_HOME}/memories/memory.md && "
            f"echo {token} > {CLAUDE_CONFIG_DIR}/projects/-app/{token}.jsonl && "
            f"echo {token} > {AGENT_HOME}/.claude/projects/-app/{token}.jsonl && "
            f'echo \'{{"note":"{token}"}}\' > {AGENT_HOME}/.claude.json'
        )
        result = await environment.exec(cmd)
        alive = await environment.exec(
            f"ps -e -o pid=,uid=,args= | grep -F {PLANT_MARKER}-{step} | grep -v grep || true"
        )
        report = {
            "kind": "raidar-step-plant",
            "step": step,
            "token": token,
            "plant_rc": result.return_code,
            "planted_process": (alive.stdout or "").strip(),
        }
        await self._write_json(environment, "/logs/agent/raidar-plant.json", report)
        return report


class ReferenceSolutionAgent(_ProbeMixin):
    """No-LLM agent that installs the step's reference payload into /app.

    Payloads come from the host-side oracle bundle and are uploaded only for
    the current step, mirroring what an agent would write. ``skip_steps`` leaves
    /app untouched for a step to force an oracle failure (halt/continue tests).
    """

    options_model: ClassVar[type[AgentOptions] | None] = ReferenceAgentOptions

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._init_probe_state()

    @staticmethod
    def name() -> str:
        return "raidar-spike-reference"

    def version(self) -> str | None:
        return "spike-1"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        step = self._resolve_step(instruction)
        await self._probe(environment, step)
        opts: ReferenceAgentOptions = self.options  # type: ignore[assignment]
        action = "skipped"
        if step not in opts.skip_steps:
            payload = Path(opts.oracle_dir) / "solutions" / step
            await environment.exec("find /app -mindepth 1 -delete")
            await environment.upload_dir(payload, "/app")
            action = "applied-reference"
        if opts.leak_step == step:
            leak_src = sorted((Path(opts.oracle_dir) / "tests").glob("test_checkpoint_*.py"))[0]
            await environment.exec("mkdir -p /tmp/negative-control")
            await environment.upload_file(leak_src, f"/tmp/negative-control/{leak_src.name}")
            action += "+leaked-hidden-test"
        if opts.plant_step == step:
            await self._plant(environment, step)
        context.metadata = {"step": step, "action": action}


class SpikeCodexAgent(_ProbeMixin):
    """Real codex-cli agent with per-step probes; credentials staged as root."""

    options_model: ClassVar[type[AgentOptions] | None] = CodexAgentOptions

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._init_probe_state()

    @staticmethod
    def name() -> str:
        return "raidar-spike-codex"

    def version(self) -> str | None:
        return None

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def _stage_auth(self, environment: BaseEnvironment) -> None:
        opts: CodexAgentOptions = self.options  # type: ignore[assignment]
        source = os.environ.get(opts.auth_json_env)
        if not source or not Path(source).is_file():
            raise RuntimeError(f"codex auth source missing: set {opts.auth_json_env}")
        staging = "/tmp/raidar-secrets/auth.json"
        await environment.exec("mkdir -p /tmp/raidar-secrets", user="root")
        await environment.upload_file(Path(source), staging)
        # docker cp leaves the file root-owned; hand it to the agent user.
        await environment.exec(
            f"install -o agent -g agent -m 600 {staging} {CODEX_HOME}/auth.json "
            f"|| (mkdir -p {CODEX_HOME} && chown agent:agent {CODEX_HOME} && "
            f"install -o agent -g agent -m 600 {staging} {CODEX_HOME}/auth.json); "
            "rm -rf /tmp/raidar-secrets",
            user="root",
        )

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        step = self._resolve_step(instruction)
        await self._probe(environment, step)
        opts: CodexAgentOptions = self.options  # type: ignore[assignment]
        await environment.exec(f"mkdir -p {CODEX_HOME}")
        await self._stage_auth(environment)
        model = (self.model_name or "").split("/", 1)[-1]
        command = (
            f"trap 'rm -f {CODEX_HOME}/auth.json' EXIT TERM INT; "
            "codex exec --ignore-user-config --disable plugins "
            "--dangerously-bypass-approvals-and-sandbox --skip-git-repo-check "
            f"--cd /app --json --model {shlex.quote(model)} "
            f"-c model_reasoning_effort={shlex.quote(opts.reasoning_effort)} "
            f"-- {shlex.quote(instruction)} > /logs/agent/codex.txt 2>&1 < /dev/null"
        )
        result = await environment.exec(command, env={"CODEX_HOME": CODEX_HOME})
        # Evidence copies of real Codex session state (before any reset).
        await environment.exec(
            f"mkdir -p /logs/agent/codex-home-snapshot && "
            f"cp -a {CODEX_HOME}/. /logs/agent/codex-home-snapshot/ 2>/dev/null; "
            "rm -f /logs/agent/codex-home-snapshot/auth.json"
        )
        thread = await environment.exec(
            'grep -o \'"thread_id":"[^"]*"\' /logs/agent/codex.txt | head -n 1 | cut -d\'"\' -f4'
        )
        thread_id = (thread.stdout or "").strip()
        if thread_id:
            # The step's real transcript/session id: later steps must not find it.
            self._tokens.append(thread_id)
        if opts.plant_step == step:
            await self._plant(environment, step)
        context.metadata = {
            "step": step,
            "return_code": result.return_code,
            "codex_thread_id": thread_id or None,
        }
