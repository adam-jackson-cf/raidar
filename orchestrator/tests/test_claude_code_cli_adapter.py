"""Tests for Claude Code CLI adapter behavior."""

import sys

import pytest

from raidar.subject.agent.config import AgentSpec, Harness, ModelTarget
from raidar.subject.agent.native import adapter_class_for_harness, resolve_adapter

ClaudeCodeCliAdapter = adapter_class_for_harness(Harness.CLAUDE_CODE)


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
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", sys.executable)
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-0"))
    with pytest.raises(ValueError, match="only supports models"):
        adapter.validate()


def test_validate_requires_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", sys.executable)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-5"))
    with pytest.raises(OSError, match="require credentials"):
        adapter.validate()


def test_validate_accepts_oauth_token_only(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", sys.executable)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-5"))
    adapter.validate()


def test_validate_rejects_unsupported_reasoning_effort(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", sys.executable)
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
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", sys.executable)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    adapter = ClaudeCodeCliAdapter(_config(model_name))
    adapter.validate()


def test_runtime_env_forwards_cli_only(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", sys.executable)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-key")
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-5"))
    env = adapter.runtime_env()
    assert env["CLAUDE_CODE_CLI_PATH"] == sys.executable
    assert "ANTHROPIC_API_KEY" not in env


@pytest.mark.parametrize(
    "credential_name",
    ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"],
)
def test_claude_credentials_preserve_the_supported_environment_name(
    monkeypatch: pytest.MonkeyPatch, credential_name: str
) -> None:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", sys.executable)
    monkeypatch.setenv(credential_name, "test-credential")
    adapter = ClaudeCodeCliAdapter(_config("claude-haiku-4-5"))

    adapter.validate()

    credentials = adapter.resolved_credentials()
    assert len(credentials) == 1
    credential = credentials[0]
    assert credential.source_name == credential_name
    assert credential.spec.transport_name == credential_name
    assert credential.spec.export_names == (credential_name,)
    assert credential_name in credential.spec.redaction.assignment_names


def test_claude_validation_rejects_the_unsupported_api_key_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_API_KEY", "test-credential")
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", sys.executable)
    adapter = ClaudeCodeCliAdapter(_config("claude-haiku-4-5"))

    with pytest.raises(OSError, match="require credentials"):
        adapter.validate()
