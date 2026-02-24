"""Tests for Claude Code CLI adapter behavior."""

from pathlib import Path

import pytest

from raidar.harness.adapters.claude_code_cli import ClaudeCodeCliAdapter
from raidar.harness.config import Agent, HarnessConfig, ModelTarget


def _config(model: str, provider: str = "anthropic") -> HarnessConfig:
    return HarnessConfig(
        agent=Agent.CLAUDE_CODE,
        model=ModelTarget(provider=provider, name=model),
    )


def test_registry_resolves_claude_adapter():
    adapter = _config("claude-sonnet-4-5").adapter()
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


def test_validate_requires_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", "/usr/local/bin/claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_API_KEY", raising=False)
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-5"))
    with pytest.raises(OSError, match="require an API key"):
        adapter.validate()


@pytest.mark.parametrize(
    "model_name",
    ("claude-opus-4-6", "claude-sonnet-4-6", "claude-sonnet-4-5", "claude-haiku-4-5"),
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


def test_prepare_workspace_creates_claude_session_dir(tmp_path: Path):
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-5"))
    adapter.prepare_workspace(tmp_path)
    assert (tmp_path / ".claude").exists()
