"""Tests for Claude Code CLI adapter behavior."""

import pytest

from raidar.subject.agent.adapters.claude_code_cli import ClaudeCodeCliAdapter
from raidar.subject.agent.adapters.factory import resolve_adapter
from raidar.subject.agent.config import AgentSpec, Harness, ModelTarget


def _config(
    model: str,
    provider: str = "anthropic",
    reasoning_effort: str | None = None,
) -> AgentSpec:
    return AgentSpec(
        harness=Harness.CLAUDE_CODE,
        model=ModelTarget(provider=provider, name=model, reasoning_effort=reasoning_effort),
    )


def test_registry_resolves_claude_adapter():
    adapter = resolve_adapter(_config("claude-sonnet-4-5"))
    assert isinstance(adapter, ClaudeCodeCliAdapter)


def test_validate_rejects_non_anthropic_provider():
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-5", provider="openai"))
    with pytest.raises(ValueError, match="provider 'anthropic'"):
        adapter.validate()


def test_validate_rejects_unsupported_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", "/usr/local/bin/claude")
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-0"))
    with pytest.raises(ValueError, match="only supports models"):
        adapter.validate()


def test_validate_requires_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", "/usr/local/bin/claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-5"))
    with pytest.raises(OSError, match="require credentials"):
        adapter.validate()


def test_validate_accepts_oauth_token_only(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", "/usr/local/bin/claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-5"))
    adapter.validate()


def test_validate_rejects_unsupported_reasoning_effort(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", "/usr/local/bin/claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-5", reasoning_effort="high"))

    with pytest.raises(ValueError, match="only supports reasoning levels: \\(none\\)"):
        adapter.validate()


@pytest.mark.parametrize(
    "model_name",
    [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
    ],
)
def test_validate_accepts_requested_models(monkeypatch: pytest.MonkeyPatch, model_name: str):
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", "/usr/local/bin/claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    adapter = ClaudeCodeCliAdapter(_config(model_name))
    adapter.validate()


def test_runtime_env_forwards_cli_only(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", "/usr/local/bin/claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_API_KEY", "test-key")
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-5"))
    env = adapter.runtime_env()
    assert env["CLAUDE_CODE_CLI_PATH"] == "/usr/local/bin/claude"
    assert "ANTHROPIC_API_KEY" not in env
