"""Tests for external CLI adapter behavior."""

import sys
from pathlib import Path

import pytest

from raidar.subject.agent.adapters.copilot_cli import CopilotCliAdapter
from raidar.subject.agent.adapters.cursor_cli import CursorCliAdapter
from raidar.subject.agent.adapters.pi_cli import PiCliAdapter
from raidar.subject.agent.config import AgentSpec, Harness, ModelTarget


def _config(harness: Harness, provider: str, model: str = "model") -> AgentSpec:
    return AgentSpec(
        harness=harness,
        model=ModelTarget(provider=provider, name=model),
    )


@pytest.mark.parametrize(
    ("adapter_class", "harness", "provider", "cli_env", "api_env", "cli_path"),
    [
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
    ],
)
def test_external_cli_adapters_validate_env_path_and_required_secret(
    monkeypatch: pytest.MonkeyPatch,
    adapter_class,
    harness: Harness,
    provider: str,
    cli_env: str,
    api_env: str,
    cli_path: str,
    tmp_path: Path,
) -> None:
    executable = tmp_path / cli_path
    executable.symlink_to(sys.executable)
    monkeypatch.setenv(cli_env, str(executable))
    monkeypatch.setenv(api_env, "test-secret")
    adapter = adapter_class(_config(harness, provider))

    adapter.validate()

    assert adapter.runtime_env()[cli_env] == str(executable)


def test_external_cli_adapter_rejects_missing_required_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CURSOR_CLI_PATH", sys.executable)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    adapter = CursorCliAdapter(_config(Harness.CURSOR, "openai"))

    with pytest.raises(OSError, match="CURSOR_API_KEY"):
        adapter.validate()


def test_external_cli_adapter_rejects_unsupported_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COPILOT_CLI_PATH", sys.executable)
    monkeypatch.setenv("COPILOT_API_KEY", "test-secret")
    adapter = CopilotCliAdapter(_config(Harness.COPILOT, "openai"))

    with pytest.raises(ValueError, match="only supports providers: github"):
        adapter.validate()


@pytest.mark.parametrize("entry", ["missing", "directory", "non_executable"])
def test_external_cli_validation_requires_an_executable_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, entry: str
) -> None:
    executable = tmp_path / "cursor"
    if entry == "directory":
        executable.mkdir()
    elif entry == "non_executable":
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o600)
    monkeypatch.setenv("CURSOR_CLI_PATH", str(executable))
    monkeypatch.setenv("CURSOR_API_KEY", "test-secret")
    adapter = CursorCliAdapter(_config(Harness.CURSOR, "openai"))

    with pytest.raises(FileNotFoundError, match="CURSOR_CLI_PATH.*executable"):
        adapter.validate()


@pytest.mark.parametrize("selection", ["absolute", "command", "default"])
def test_external_cli_resolution_preserves_executable_paths_with_spaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, selection: str
) -> None:
    directory = tmp_path / "agent tools"
    directory.mkdir()
    executable = directory / "cursor"
    executable.symlink_to(sys.executable)
    monkeypatch.setenv("PATH", str(directory))
    monkeypatch.setenv("CURSOR_API_KEY", "test-secret")
    if selection == "default":
        monkeypatch.delenv("CURSOR_CLI_PATH", raising=False)
    else:
        override = str(executable) if selection == "absolute" else executable.name
        monkeypatch.setenv("CURSOR_CLI_PATH", override)
    adapter = CursorCliAdapter(_config(Harness.CURSOR, "openai"))

    adapter.validate()

    assert adapter.runtime_env()["CURSOR_CLI_PATH"] == str(executable)
