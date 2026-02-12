"""Custom Harbor agents with no setup phase for smoke runs."""

from __future__ import annotations

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


class FastGeminiCliAgent(BaseAgent):
    """Gemini CLI agent that assumes binary is already available in the image."""

    @staticmethod
    def name() -> str:
        return "fast-gemini-cli"

    def version(self) -> str | None:
        return None

    async def setup(self, environment: BaseEnvironment) -> None:
        del environment
        return None

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        model = _model_name(self.model_name)
        escaped_instruction = shlex.quote(instruction)
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
            await _upload_secret_file(environment, secret_value=gemini_key, target_path=path)
            secret_paths["GEMINI_API_KEY"] = path
        google_key = _secret_from_file_env("GOOGLE_API_KEY")
        if google_key:
            path = "/tmp/agentic-eval-secrets/google_api_key"
            await _upload_secret_file(environment, secret_value=google_key, target_path=path)
            secret_paths["GOOGLE_API_KEY"] = path

        secret_prefix = _secret_export_prefix(secret_paths)
        result = await environment.exec(
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
        _set_context_metadata(context, result.return_code, "/logs/agent/gemini-cli.txt")


class FastClaudeCodeCliAgent(BaseAgent):
    """Claude Code CLI agent that skips install/setup and executes directly."""

    _ALLOWED_TOOLS = (
        "Bash Edit Write Read Glob Grep LS WebFetch NotebookEdit "
        "NotebookRead TodoRead TodoWrite Agent Skill SlashCommand Task WebSearch"
    )

    @staticmethod
    def name() -> str:
        return "fast-claude-code"

    def version(self) -> str | None:
        return None

    async def setup(self, environment: BaseEnvironment) -> None:
        del environment
        return None

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        escaped_instruction = shlex.quote(instruction)
        env: dict[str, str] = {
            "FORCE_AUTO_BACKGROUND_TASKS": "1",
            "ENABLE_BACKGROUND_TASKS": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CONFIG_DIR": "/logs/agent/sessions",
        }
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
        for key in (
            "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
            "MAX_THINKING_TOKENS",
        ):
            value = os.environ.get(key)
            if value:
                env[key] = value

        if "ANTHROPIC_BASE_URL" in env:
            env["ANTHROPIC_MODEL"] = self.model_name or env.get("ANTHROPIC_MODEL", "")
            if env["ANTHROPIC_MODEL"]:
                env["CLAUDE_CODE_SUBAGENT_MODEL"] = env["ANTHROPIC_MODEL"]
                env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = env["ANTHROPIC_MODEL"]
                env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = env["ANTHROPIC_MODEL"]
                env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = env["ANTHROPIC_MODEL"]
        else:
            model = _model_name(self.model_name)
            env["ANTHROPIC_MODEL"] = model

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
            f"-p {escaped_instruction} --allowedTools {self._ALLOWED_TOOLS} "
            "2>&1 </dev/null | tee /logs/agent/claude-code.txt"
        )
        result = await environment.exec(
            command=claude_command,
            env=env,
        )
        _set_context_metadata(context, result.return_code, "/logs/agent/claude-code.txt")


class FastCodexCliAgent(BaseAgent):
    """Codex CLI agent that skips install/setup and executes directly."""

    def __init__(self, reasoning_effort: str | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._reasoning_effort = reasoning_effort

    @staticmethod
    def name() -> str:
        return "fast-codex"

    def version(self) -> str | None:
        return None

    async def setup(self, environment: BaseEnvironment) -> None:
        del environment
        return None

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        model = _model_name(self.model_name)
        escaped_instruction = shlex.quote(instruction)
        env = {
            "CODEX_HOME": "/logs/agent/codex-home",
        }
        openai_key = _secret_from_file_env("OPENAI_API_KEY") or ""
        await _upload_secret_file(
            environment,
            secret_value=openai_key,
            target_path="/tmp/agentic-eval-secrets/openai_api_key",
        )
        reasoning_flag = ""
        if self._reasoning_effort:
            reasoning_flag = f"-c model_reasoning_effort={shlex.quote(self._reasoning_effort)} "

        await environment.exec(
            command=(
                'mkdir -p /tmp/codex-secrets "$CODEX_HOME" && '
                'printf \'{\\n  "OPENAI_API_KEY": "%s"\\n}\\n\' '
                '"$(cat /tmp/agentic-eval-secrets/openai_api_key)" '
                "> /tmp/codex-secrets/auth.json && "
                'ln -sf /tmp/codex-secrets/auth.json "$CODEX_HOME/auth.json"'
            ),
            env=env,
        )
        result = await environment.exec(
            command=(
                "trap 'rm -rf /tmp/codex-secrets \"$CODEX_HOME/auth.json\"' EXIT TERM INT; "
                "codex exec --dangerously-bypass-approvals-and-sandbox "
                "--skip-git-repo-check --json "
                f"--model {shlex.quote(model)} {reasoning_flag}"
                f"-- {escaped_instruction} "
                "2>&1 </dev/null | tee /logs/agent/codex.txt"
            ),
            env=env,
        )
        _set_context_metadata(context, result.return_code, "/logs/agent/codex.txt")
