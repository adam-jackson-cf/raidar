"""Repository-local Harbor agents for supported CLI harnesses."""

from __future__ import annotations

import json
import os
import shlex
import tempfile
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

SECRET_FILE_ENV_PREFIX = "AGENTIC_EVAL_SECRET_FILE_"


def _model_name(model_name: str | None) -> str:
    if not model_name:
        raise ValueError("Model name is required.")
    return model_name.split("/", 1)[-1]


def _set_context_metadata(context: AgentContext, return_code: int, log_file: str) -> None:
    context.metadata = {"return_code": return_code, "log_file": log_file}


class CliHarborAgentBase(BaseAgent):
    """Shared lifecycle and execution helpers for repository-local CLI agents."""

    log_file: str = ""

    def version(self) -> str | None:
        return None

    async def setup(self, environment: BaseEnvironment) -> None:
        del environment
        return None

    def _model_name(self) -> str:
        return _model_name(self.model_name)

    def _quote_instruction(self, instruction: str) -> str:
        return shlex.quote(instruction)

    async def _exec_agent_command(
        self,
        environment: BaseEnvironment,
        context: AgentContext,
        *,
        command: str,
        env: dict[str, str],
        log_file: str | None = None,
    ) -> None:
        resolved_log_file = log_file or self.log_file
        result = await environment.exec(command=command, env=env)
        _set_context_metadata(context, result.return_code, resolved_log_file)


async def _upload_secret_file(
    environment: BaseEnvironment,
    *,
    secret_value: str,
    target_path: str,
) -> None:
    tmp_path: Path | None = None
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp_file:
        tmp_file.write(secret_value)
        tmp_path = Path(tmp_file.name)
    try:
        await environment.exec(
            command=f"mkdir -p {shlex.quote(str(Path(target_path).parent))}",
        )
        await environment.upload_file(tmp_path, target_path)
        await environment.exec(command=f"chmod 600 {shlex.quote(target_path)}")
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _secret_export_prefix(secret_paths: dict[str, str]) -> str:
    if not secret_paths:
        return ""
    assignments = [f'{name}="$(cat {shlex.quote(path)})"' for name, path in secret_paths.items()]
    return " ".join(assignments) + " "


def _secret_from_file_env(secret_name: str) -> str | None:
    secret_file = os.environ.get(f"{SECRET_FILE_ENV_PREFIX}{secret_name}")
    if not secret_file:
        return None
    secret_path = Path(secret_file)
    if not secret_path.exists():
        return None
    return secret_path.read_text(encoding="utf-8").rstrip("\n")


async def _claude_secret_paths(environment: BaseEnvironment) -> dict[str, str]:
    secret_paths: dict[str, str] = {}
    anthropic_key = _secret_from_file_env("ANTHROPIC_API_KEY")
    if not anthropic_key:
        anthropic_key = _secret_from_file_env("CLAUDE_CODE_API_KEY")
    if anthropic_key:
        path = "/tmp/agentic-eval-secrets/anthropic_api_key"
        await _upload_secret_file(environment, secret_value=anthropic_key, target_path=path)
        secret_paths["ANTHROPIC_API_KEY"] = path
        secret_paths["CLAUDE_CODE_API_KEY"] = path
    oauth_token = _secret_from_file_env("CLAUDE_CODE_OAUTH_TOKEN")
    if oauth_token:
        path = "/tmp/agentic-eval-secrets/claude_code_oauth_token"
        await _upload_secret_file(environment, secret_value=oauth_token, target_path=path)
        secret_paths["CLAUDE_CODE_OAUTH_TOKEN"] = path
    return secret_paths


def _claude_base_env() -> dict[str, str]:
    env: dict[str, str] = {
        "FORCE_AUTO_BACKGROUND_TASKS": "1",
        "ENABLE_BACKGROUND_TASKS": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CONFIG_DIR": "/logs/agent/sessions",
    }
    for key in (
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "MAX_THINKING_TOKENS",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _configure_claude_model_env(env: dict[str, str], model_name: str | None) -> None:
    if "ANTHROPIC_BASE_URL" in env:
        env["ANTHROPIC_MODEL"] = model_name or env.get("ANTHROPIC_MODEL", "")
        if env["ANTHROPIC_MODEL"]:
            env["CLAUDE_CODE_SUBAGENT_MODEL"] = env["ANTHROPIC_MODEL"]
            env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = env["ANTHROPIC_MODEL"]
            env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = env["ANTHROPIC_MODEL"]
            env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = env["ANTHROPIC_MODEL"]
        return
    env["ANTHROPIC_MODEL"] = _model_name(model_name)


def _claude_settings_flag(*, effort: str | None, thinking_mode: str | None) -> str:
    if not (effort or thinking_mode):
        return ""
    settings_payload: dict[str, object] = {}
    if thinking_mode:
        settings_payload["thinking"] = {"type": thinking_mode}
    if effort:
        settings_payload["output_config"] = {"effort": effort}
    return f"--settings {shlex.quote(json.dumps(settings_payload))} "


class GeminiCliHarborAgent(CliHarborAgentBase):
    """Gemini CLI Harbor agent that assumes the binary is available in the image."""

    log_file = "/logs/agent/gemini-cli.txt"

    @staticmethod
    def name() -> str:
        return "gemini-cli-harbor"

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        model = self._model_name()
        escaped_instruction = self._quote_instruction(instruction)
        env: dict[str, str] = {}
        for key in (
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "GOOGLE_GENAI_USE_VERTEXAI",
        ):
            value = os.environ.get(key)
            if value:
                env[key] = value
        secret_paths: dict[str, str] = {}
        gemini_key = _secret_from_file_env("GEMINI_API_KEY")
        if gemini_key:
            path = "/tmp/agentic-eval-secrets/gemini_api_key"
            await _upload_secret_file(
                environment,
                secret_value=gemini_key,
                target_path=path,
            )
            secret_paths["GEMINI_API_KEY"] = path

        secret_prefix = _secret_export_prefix(secret_paths)
        await self._exec_agent_command(
            environment,
            context,
            command=(
                f"{secret_prefix}gemini -p {escaped_instruction} -y -m {model} "
                "2>&1 </dev/null | tee /logs/agent/gemini-cli.txt"
            ),
            env=env,
        )
        await environment.exec(
            command=(
                "find ~/.gemini/tmp -type f -name 'session-*.json' 2>/dev/null "
                "| head -n 1 | xargs -r -I{} cp {} /logs/agent/gemini-cli.trajectory.json"
            )
        )


class ClaudeCodeCliHarborAgent(CliHarborAgentBase):
    """Claude Code CLI Harbor agent that executes directly in the task image."""

    log_file = "/logs/agent/claude-code.txt"
    _ALLOWED_TOOLS = (
        "Bash Edit Write Read Glob Grep LS WebFetch NotebookEdit "
        "NotebookRead TodoRead TodoWrite Agent Skill SlashCommand Task WebSearch"
    )

    def __init__(
        self,
        effort: str | None = None,
        thinking_mode: str | None = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._effort = effort
        self._thinking_mode = thinking_mode

    @staticmethod
    def name() -> str:
        return "claude-code-harbor"

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        escaped_instruction = self._quote_instruction(instruction)
        env = _claude_base_env()
        secret_paths = await _claude_secret_paths(environment)
        _configure_claude_model_env(env, self.model_name)
        settings_flag = _claude_settings_flag(
            effort=self._effort,
            thinking_mode=self._thinking_mode,
        )

        await environment.exec(
            command=(
                "mkdir -p $CLAUDE_CONFIG_DIR/debug $CLAUDE_CONFIG_DIR/projects/-app "
                "$CLAUDE_CONFIG_DIR/shell-snapshots $CLAUDE_CONFIG_DIR/statsig "
                "$CLAUDE_CONFIG_DIR/todos && "
                "if [ -d ~/.claude/skills ]; then "
                "cp -r ~/.claude/skills $CLAUDE_CONFIG_DIR/skills 2>/dev/null || true; "
                "fi"
            ),
            env=env,
        )
        secret_prefix = _secret_export_prefix(secret_paths)
        claude_command = (
            f"{secret_prefix}claude --verbose --output-format stream-json "
            f"{settings_flag}-p {escaped_instruction} --allowedTools {self._ALLOWED_TOOLS} "
            "2>&1 </dev/null | tee /logs/agent/claude-code.txt"
        )
        await self._exec_agent_command(
            environment,
            context,
            command=claude_command,
            env=env,
        )


class CodexCliHarborAgent(CliHarborAgentBase):
    """Codex CLI Harbor agent that executes directly in the task image."""

    log_file = "/logs/agent/codex.txt"

    def __init__(self, reasoning_effort: str | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._reasoning_effort = reasoning_effort

    @staticmethod
    def name() -> str:
        return "codex-cli-harbor"

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        model = self._model_name()
        escaped_instruction = self._quote_instruction(instruction)
        env = {
            "CODEX_HOME": "/logs/agent/codex-home",
        }
        auth_json = _secret_from_file_env("CODEX_AUTH_JSON")
        openai_key = _secret_from_file_env("OPENAI_API_KEY") or ""
        reasoning_flag = ""
        if self._reasoning_effort:
            reasoning_flag = f"-c model_reasoning_effort={shlex.quote(self._reasoning_effort)} "

        if auth_json:
            await _upload_secret_file(
                environment,
                secret_value=auth_json,
                target_path="/tmp/agentic-eval-secrets/codex_auth_json",
            )
            await environment.exec(
                command=(
                    'mkdir -p /tmp/codex-secrets "$CODEX_HOME" && '
                    "cp /tmp/agentic-eval-secrets/codex_auth_json /tmp/codex-secrets/auth.json && "
                    "chmod 600 /tmp/codex-secrets/auth.json && "
                    'ln -sf /tmp/codex-secrets/auth.json "$CODEX_HOME/auth.json"'
                ),
                env=env,
            )
        else:
            await _upload_secret_file(
                environment,
                secret_value=openai_key,
                target_path="/tmp/agentic-eval-secrets/openai_api_key",
            )
            await environment.exec(
                command=(
                    'mkdir -p /tmp/codex-secrets "$CODEX_HOME" && '
                    'printf \'{\\n  "OPENAI_API_KEY": "%s"\\n}\\n\' '
                    '"$(cat /tmp/agentic-eval-secrets/openai_api_key)" '
                    "> /tmp/codex-secrets/auth.json && "
                    "chmod 600 /tmp/codex-secrets/auth.json && "
                    'ln -sf /tmp/codex-secrets/auth.json "$CODEX_HOME/auth.json"'
                ),
                env=env,
            )
        await self._exec_agent_command(
            environment,
            context,
            command=(
                "trap 'rm -rf /tmp/codex-secrets \"$CODEX_HOME/auth.json\"' EXIT TERM INT; "
                "codex exec --ignore-user-config --ephemeral "
                "--disable plugins "
                "--dangerously-bypass-approvals-and-sandbox "
                "--skip-git-repo-check --cd /app --json "
                f"--model {shlex.quote(model)} {reasoning_flag}"
                f"-- {escaped_instruction} "
                "> /logs/agent/codex.txt 2>&1 </dev/null"
            ),
            env=env,
        )
