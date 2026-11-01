"""Claude Code CLI adapter: what it refuses before a trial and what it hands to Harbor."""

import sys
from pathlib import Path

import pytest

from raidar.subject.agent import harness as harness_module
from raidar.subject.agent.config import AgentSpec, Harness, ModelTarget
from raidar.subject.agent.native import adapter_class_for_harness

ClaudeCodeCliAdapter = adapter_class_for_harness(Harness.CLAUDE_CODE)
CREDENTIAL_NAMES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_API_KEY",
)


def _config(
    model: str,
    provider: str = "anthropic",
    reasoning_effort: str | None = None,
) -> AgentSpec:
    return AgentSpec(
        harness=Harness.CLAUDE_CODE,
        model=ModelTarget(provider=provider, name=model, reasoning_effort=reasoning_effort),
    )


def _host(monkeypatch: pytest.MonkeyPatch, **credentials: str) -> None:
    """A host with the claude CLI installed and exactly the given credentials set."""
    for name in CREDENTIAL_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", sys.executable)
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)


def test_validate_rejects_non_anthropic_provider() -> None:
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-5", provider="openai"))
    with pytest.raises(ValueError, match="provider 'anthropic'"):
        adapter.validate()


def test_validate_rejects_unsupported_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch, ANTHROPIC_API_KEY="test-key")
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-0"))
    with pytest.raises(ValueError, match="only supports models"):
        adapter.validate()


def test_validate_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch)
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-5"))
    with pytest.raises(OSError, match="require credentials"):
        adapter.validate()


def test_validate_refuses_a_host_without_the_claude_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _host(monkeypatch, ANTHROPIC_API_KEY="test-key")
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", str(tmp_path / "absent"))
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-5"))
    with pytest.raises(FileNotFoundError, match="CLAUDE_CODE_CLI_PATH"):
        adapter.validate()


@pytest.mark.parametrize(
    ("model", "effort", "allowed"),
    [
        ("claude-sonnet-4-5", "high", False),  # a model with no reasoning levels
        ("claude-sonnet-4-6", "xhigh", False),  # a level that model does not have
        ("claude-sonnet-4-6", "high", True),
        ("claude-opus-4-7", "xhigh", True),
        ("claude-haiku-4-5", None, True),
    ],
)
def test_reasoning_effort_must_be_one_the_model_supports(
    monkeypatch: pytest.MonkeyPatch, model: str, effort: str | None, allowed: bool
) -> None:
    _host(monkeypatch, ANTHROPIC_API_KEY="test-key")
    adapter = ClaudeCodeCliAdapter(_config(model, reasoning_effort=effort))

    if allowed:
        adapter.validate()
    else:
        with pytest.raises(ValueError, match="only supports reasoning levels"):
            adapter.validate()


def test_runtime_env_forwards_the_cli_path_and_no_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch, ANTHROPIC_AUTH_TOKEN="test-key")
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-5"))

    assert adapter.runtime_env() == {"CLAUDE_CODE_CLI_PATH": sys.executable}


@pytest.mark.parametrize(
    "credential_name",
    ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"],
)
def test_claude_credentials_preserve_the_supported_environment_name(
    monkeypatch: pytest.MonkeyPatch, credential_name: str
) -> None:
    _host(monkeypatch, **{credential_name: "test-credential"})
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
    _host(monkeypatch, CLAUDE_CODE_API_KEY="test-credential")
    adapter = ClaudeCodeCliAdapter(_config("claude-haiku-4-5"))

    with pytest.raises(OSError, match="require credentials"):
        adapter.validate()


def test_model_summary_lists_every_supported_model_under_the_provider() -> None:
    summary = ClaudeCodeCliAdapter.supported_model_summary().split(", ")

    assert summary == sorted(summary)
    assert all(item.startswith("anthropic/claude-") for item in summary)
    assert "anthropic/claude-haiku-4-5" in summary


def test_a_required_credential_outside_any_group_is_named_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch)
    token = harness_module.CredentialSpec(
        id="solo-token",
        accepted_names=("SOLO_TOKEN",),
        source_type=harness_module.CredentialSourceType.ENVIRONMENT,
        required=True,
        transport=harness_module.CredentialTransport.SECRET_FILE,
        transport_name="SOLO_TOKEN",
        mount_path="/tmp/solo",
        redaction=harness_module.CredentialRedaction(assignment_names=("SOLO_TOKEN",)),
    )
    definition = harness_module.HarnessDefinition("claude-code", "CLAUDE.md", (token,))
    monkeypatch.setattr(
        "raidar.subject.agent.adapters.base.harness_definition", lambda _harness: definition
    )
    monkeypatch.delenv("SOLO_TOKEN", raising=False)
    adapter = ClaudeCodeCliAdapter(_config("claude-haiku-4-5"))

    with pytest.raises(OSError, match="set: SOLO_TOKEN"):
        adapter.validate()

    monkeypatch.setenv("SOLO_TOKEN", "present")
    adapter.validate()


def test_claude_declares_no_execution_metadata_and_the_harbor_harness_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch, ANTHROPIC_API_KEY="test-key")
    adapter = ClaudeCodeCliAdapter(_config("claude-sonnet-4-5"))

    assert adapter.execution_metadata() == {}
    assert adapter.harbor_harness() == "claude-code"
    assert adapter.model_argument() == "anthropic/claude-sonnet-4-5"
