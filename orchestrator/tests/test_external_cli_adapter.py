"""Tests for external CLI adapter behavior."""

import pytest

from raidar.agents.adapters.copilot_cli import CopilotCliAdapter
from raidar.agents.adapters.cursor_cli import CursorCliAdapter
from raidar.agents.adapters.pi_cli import PiCliAdapter
from raidar.agents.config import AgentSpec, Harness, ModelTarget


def _config(harness: Harness, provider: str, model: str = "model") -> AgentSpec:
    return AgentSpec(
        harness=harness,
        model=ModelTarget(provider=provider, name=model),
    )


@pytest.mark.parametrize(
    ("adapter_class", "harness", "provider", "cli_env", "api_env", "cli_path"),
    (
        (CursorCliAdapter, Harness.CURSOR, "openai", "CURSOR_CLI_PATH", "CURSOR_API_KEY", "cursor"),
        (
            CopilotCliAdapter,
            Harness.COPILOT,
            "github",
            "COPILOT_CLI_PATH",
            "COPILOT_API_KEY",
            "copilot",
        ),
        (PiCliAdapter, Harness.PI, "inflection", "PI_CLI_PATH", "PI_API_TOKEN", "pi"),
    ),
)
def test_external_cli_adapters_validate_env_path_and_required_secret(
    monkeypatch: pytest.MonkeyPatch,
    adapter_class,
    harness: Harness,
    provider: str,
    cli_env: str,
    api_env: str,
    cli_path: str,
) -> None:
    monkeypatch.setenv(cli_env, f"/usr/local/bin/{cli_path}")
    monkeypatch.setenv(api_env, "test-secret")
    adapter = adapter_class(_config(harness, provider))

    adapter.validate()

    assert adapter.runtime_env()[cli_env] == f"/usr/local/bin/{cli_path}"


def test_external_cli_adapter_rejects_missing_required_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CURSOR_CLI_PATH", "/usr/local/bin/cursor")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    adapter = CursorCliAdapter(_config(Harness.CURSOR, "openai"))

    with pytest.raises(OSError, match="CURSOR_API_KEY"):
        adapter.validate()


def test_external_cli_adapter_rejects_unsupported_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COPILOT_CLI_PATH", "/usr/local/bin/copilot")
    monkeypatch.setenv("COPILOT_API_KEY", "test-secret")
    adapter = CopilotCliAdapter(_config(Harness.COPILOT, "openai"))

    with pytest.raises(ValueError, match="only supports providers: github"):
        adapter.validate()
