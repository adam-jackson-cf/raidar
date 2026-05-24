import importlib
import sys
import types
from pathlib import Path

import pytest


class _BaseAgent:
    def __init__(self, model_name: str | None = None, **_kwargs):
        self.model_name = model_name


class _ExecResult:
    def __init__(self, return_code: int = 0):
        self.return_code = return_code


class _Environment:
    def __init__(self) -> None:
        self.exec_calls: list[dict[str, object]] = []
        self.uploads: list[tuple[Path, str, str]] = []

    async def exec(self, *, command: str, env: dict[str, str] | None = None) -> _ExecResult:
        self.exec_calls.append({"command": command, "env": env or {}})
        return _ExecResult()

    async def upload_file(self, source: Path, target_path: str) -> None:
        self.uploads.append((source, target_path, source.read_text(encoding="utf-8")))


class _Context:
    metadata: dict[str, object]


@pytest.fixture
def harbor_cli_agents(monkeypatch):
    modules = {
        "harbor": types.ModuleType("harbor"),
        "harbor.agents": types.ModuleType("harbor.agents"),
        "harbor.agents.base": types.ModuleType("harbor.agents.base"),
        "harbor.environments": types.ModuleType("harbor.environments"),
        "harbor.environments.base": types.ModuleType("harbor.environments.base"),
        "harbor.models": types.ModuleType("harbor.models"),
        "harbor.models.agent": types.ModuleType("harbor.models.agent"),
        "harbor.models.agent.context": types.ModuleType("harbor.models.agent.context"),
    }
    modules["harbor.agents.base"].BaseAgent = _BaseAgent
    modules["harbor.environments.base"].BaseEnvironment = object
    modules["harbor.models.agent.context"].AgentContext = _Context
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    sys.modules.pop("raidar.agents.harbor_agents", None)
    sys.modules.pop("raidar.agents.harbor_agents.cli_agents", None)
    return importlib.import_module("raidar.agents.harbor_agents.cli_agents")


def test_harbor_agents_package_exports_supported_cli_agents(harbor_cli_agents):
    package = importlib.import_module("raidar.agents.harbor_agents")

    assert package.CodexCliHarborAgent is harbor_cli_agents.CodexCliHarborAgent
    assert package.ClaudeCodeCliHarborAgent is harbor_cli_agents.ClaudeCodeCliHarborAgent
    assert package.GeminiCliHarborAgent is harbor_cli_agents.GeminiCliHarborAgent


def test_model_name_requires_configured_model(harbor_cli_agents):
    assert harbor_cli_agents._model_name("openai/gpt-5.5") == "gpt-5.5"

    with pytest.raises(ValueError, match="Model name is required"):
        harbor_cli_agents._model_name(None)


@pytest.mark.asyncio
async def test_secret_upload_writes_remote_file_and_removes_local_temp(harbor_cli_agents):
    environment = _Environment()

    await harbor_cli_agents._upload_secret_file(
        environment,
        secret_value="secret-value",
        target_path="/tmp/agentic-eval-secrets/token",
    )

    assert [call["command"] for call in environment.exec_calls] == [
        "mkdir -p /tmp/agentic-eval-secrets",
        "chmod 600 /tmp/agentic-eval-secrets/token",
    ]
    [(source, target, content)] = environment.uploads
    assert target == "/tmp/agentic-eval-secrets/token"
    assert content == "secret-value"
    assert not source.exists()


def test_secret_file_env_reads_existing_secret_and_ignores_missing_path(
    harbor_cli_agents, monkeypatch, tmp_path: Path
):
    secret_path = tmp_path / "secret.txt"
    secret_path.write_text("value\n", encoding="utf-8")
    monkeypatch.setenv("AGENTIC_EVAL_SECRET_FILE_OPENAI_API_KEY", str(secret_path))
    monkeypatch.setenv("AGENTIC_EVAL_SECRET_FILE_MISSING", str(tmp_path / "missing"))

    assert harbor_cli_agents._secret_from_file_env("OPENAI_API_KEY") == "value"
    assert harbor_cli_agents._secret_from_file_env("MISSING") is None
    assert harbor_cli_agents._secret_from_file_env("UNSET") is None


def test_secret_export_prefix_and_claude_settings_are_empty_without_inputs(harbor_cli_agents):
    assert harbor_cli_agents._secret_export_prefix({}) == ""
    assert harbor_cli_agents._claude_settings_flag(effort=None, thinking_mode=None) == ""


@pytest.mark.asyncio
async def test_claude_secret_paths_accepts_legacy_claude_key_env(
    harbor_cli_agents, monkeypatch, tmp_path: Path
):
    key_path = tmp_path / "claude-code-key"
    key_path.write_text("legacy-secret", encoding="utf-8")
    monkeypatch.setenv("AGENTIC_EVAL_SECRET_FILE_CLAUDE_CODE_API_KEY", str(key_path))
    monkeypatch.delenv("AGENTIC_EVAL_SECRET_FILE_ANTHROPIC_API_KEY", raising=False)
    environment = _Environment()

    paths = await harbor_cli_agents._claude_secret_paths(environment)

    assert paths == {
        "ANTHROPIC_API_KEY": "/tmp/agentic-eval-secrets/anthropic_api_key",
        "CLAUDE_CODE_API_KEY": "/tmp/agentic-eval-secrets/anthropic_api_key",
    }


@pytest.mark.asyncio
async def test_gemini_agent_exports_secret_and_copies_trajectory(
    harbor_cli_agents, monkeypatch, tmp_path: Path
):
    secret_path = tmp_path / "gemini-key"
    secret_path.write_text("gemini-secret", encoding="utf-8")
    monkeypatch.setenv("AGENTIC_EVAL_SECRET_FILE_GEMINI_API_KEY", str(secret_path))
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-one")

    environment = _Environment()
    context = _Context()
    agent = harbor_cli_agents.GeminiCliHarborAgent(model_name="google/gemini-2.5-pro")

    assert agent.name() == "gemini-cli-harbor"
    assert agent.version() is None
    assert await agent.setup(environment) is None
    await agent.run("build it", environment, context)

    command = str(environment.exec_calls[2]["command"])
    assert 'GEMINI_API_KEY="$(cat /tmp/agentic-eval-secrets/gemini_api_key)"' in command
    assert "gemini -p 'build it' -y -m gemini-2.5-pro" in command
    assert "cp {} /logs/agent/gemini-cli.trajectory.json" in str(
        environment.exec_calls[3]["command"]
    )
    assert environment.exec_calls[2]["env"] == {"GOOGLE_CLOUD_PROJECT": "project-one"}
    assert context.metadata == {
        "return_code": 0,
        "log_file": "/logs/agent/gemini-cli.txt",
    }


@pytest.mark.asyncio
async def test_claude_agent_configures_model_env_settings_and_secret_paths(
    harbor_cli_agents, monkeypatch, tmp_path: Path
):
    key_path = tmp_path / "anthropic-key"
    oauth_path = tmp_path / "oauth"
    key_path.write_text("anthropic-secret", encoding="utf-8")
    oauth_path.write_text("oauth-secret", encoding="utf-8")
    monkeypatch.setenv("AGENTIC_EVAL_SECRET_FILE_ANTHROPIC_API_KEY", str(key_path))
    monkeypatch.setenv("AGENTIC_EVAL_SECRET_FILE_CLAUDE_CODE_OAUTH_TOKEN", str(oauth_path))
    monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "4096")

    environment = _Environment()
    context = _Context()
    agent = harbor_cli_agents.ClaudeCodeCliHarborAgent(
        model_name="anthropic/claude-sonnet-4.5",
        effort="low",
        thinking_mode="enabled",
    )

    assert agent.name() == "claude-code-harbor"
    await agent.run("change app", environment, context)

    setup_call = environment.exec_calls[4]
    command_call = environment.exec_calls[5]
    assert "$CLAUDE_CONFIG_DIR/debug" in str(setup_call["command"])
    env = command_call["env"]
    assert env["ANTHROPIC_MODEL"] == "claude-sonnet-4.5"
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4096"
    command = str(command_call["command"])
    assert 'ANTHROPIC_API_KEY="$(cat /tmp/agentic-eval-secrets/anthropic_api_key)"' in command
    assert (
        'CLAUDE_CODE_OAUTH_TOKEN="$(cat /tmp/agentic-eval-secrets/claude_code_oauth_token)"'
        in command
    )
    assert "--settings" in command
    assert "--allowedTools" in command
    assert context.metadata["log_file"] == "/logs/agent/claude-code.txt"


def test_claude_model_env_uses_base_url_override(harbor_cli_agents, monkeypatch):
    env = {"ANTHROPIC_BASE_URL": "http://localhost:4000", "ANTHROPIC_MODEL": "default"}

    harbor_cli_agents._configure_claude_model_env(env, None)

    assert env["ANTHROPIC_MODEL"] == "default"
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == "default"
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "default"

    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="Model name is required"):
        harbor_cli_agents._configure_claude_model_env({}, None)


@pytest.mark.asyncio
async def test_codex_agent_uses_oauth_auth_json_when_available(
    harbor_cli_agents, monkeypatch, tmp_path: Path
):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"tokens":true}', encoding="utf-8")
    monkeypatch.setenv("AGENTIC_EVAL_SECRET_FILE_CODEX_AUTH_JSON", str(auth_path))

    environment = _Environment()
    context = _Context()
    agent = harbor_cli_agents.CodexCliHarborAgent(
        model_name="codex/gpt-5.5",
        reasoning_effort="medium",
    )

    assert agent.name() == "codex-cli-harbor"
    await agent.run("implement", environment, context)

    assert "/tmp/agentic-eval-secrets/codex_auth_json" in [
        target for _source, target, _content in environment.uploads
    ]
    assert "ln -sf /tmp/codex-secrets/auth.json" in str(environment.exec_calls[2]["command"])
    command = str(environment.exec_calls[3]["command"])
    assert "--model gpt-5.5 -c model_reasoning_effort=medium -- implement" in command
    assert context.metadata == {"return_code": 0, "log_file": "/logs/agent/codex.txt"}


@pytest.mark.asyncio
async def test_codex_agent_builds_auth_json_from_api_key_secret(
    harbor_cli_agents, monkeypatch, tmp_path: Path
):
    key_path = tmp_path / "openai-key"
    key_path.write_text("openai-secret", encoding="utf-8")
    monkeypatch.setenv("AGENTIC_EVAL_SECRET_FILE_OPENAI_API_KEY", str(key_path))
    monkeypatch.delenv("AGENTIC_EVAL_SECRET_FILE_CODEX_AUTH_JSON", raising=False)

    environment = _Environment()
    context = _Context()
    agent = harbor_cli_agents.CodexCliHarborAgent(model_name="openai/gpt-5.5")

    await agent.run("ship", environment, context)

    assert "/tmp/agentic-eval-secrets/openai_api_key" in [
        target for _source, target, _content in environment.uploads
    ]
    assert '"OPENAI_API_KEY": "%s"' in str(environment.exec_calls[2]["command"])
    assert "model_reasoning_effort" not in str(environment.exec_calls[3]["command"])
