"""Codex CLI adapter: auth-mode precedence, refusals before a trial and what it hands to Harbor."""

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from raidar.subject.agent import executables
from raidar.subject.agent.config import AgentSpec, Harness, ModelTarget
from raidar.subject.agent.native import adapter_class_for_harness

CODEX_AUTH_MODE_ENV = "CODEX_AUTH_MODE"
CodexCliAdapter = adapter_class_for_harness(Harness.CODEX_CLI)


def _config(
    model: str,
    provider: str = "openai",
    reasoning_effort: str | None = None,
) -> AgentSpec:
    return AgentSpec(
        harness=Harness.CODEX_CLI,
        model=ModelTarget(provider=provider, name=model, reasoning_effort=reasoning_effort),
    )


def _adapter(model: str = "gpt-5.4", reasoning_effort: str | None = "high") -> Any:
    return CodexCliAdapter(_config(model, reasoning_effort=reasoning_effort))


def _write_codex_auth(tmp_path: Path, text: str | None = None) -> Path:
    auth_dir = tmp_path / ".codex"
    auth_dir.mkdir()
    auth_path = auth_dir / "auth.json"
    auth_path.write_text(text or json.dumps({"id_token": "token"}), encoding="utf-8")
    return auth_path


def _host(
    monkeypatch: pytest.MonkeyPatch,
    *,
    home: Path | None = None,
    key: str | None = None,
    mode: str | None = None,
) -> None:
    """A host with the codex CLI installed, the given login home, API key and auth mode."""
    monkeypatch.setenv("CODEX_CLI_PATH", sys.executable)
    for name, value in (
        ("CODEX_HOME", str(home) if home else None),
        ("OPENAI_API_KEY", key),
        (CODEX_AUTH_MODE_ENV, mode),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def test_validate_rejects_non_codex_provider() -> None:
    adapter = CodexCliAdapter(_config("gpt-5.4", provider="codex", reasoning_effort="high"))
    with pytest.raises(ValueError, match="provider 'openai'"):
        adapter.validate()


def test_validate_requires_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _host(monkeypatch, home=tmp_path / ".codex")
    with pytest.raises(OSError, match="make codex-auth-setup"):
        _adapter().validate()


def test_runtime_env_forwards_the_cli_path_and_no_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch, key="test-key")

    assert _adapter().runtime_env() == {"CODEX_CLI_PATH": sys.executable}


def test_validate_accepts_file_backed_chatgpt_auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_path = _write_codex_auth(tmp_path)
    _host(monkeypatch, home=auth_path.parent)
    adapter = _adapter()

    adapter.validate()

    assert adapter.execution_metadata()["auth_mode"] == "chatgpt"
    assert adapter.harbor_harness() == "codex"
    assert adapter.credential_file_sources() == {"CODEX_AUTH_JSON": auth_path}
    assert adapter.credential_environment_sources() == {}


def test_auto_mode_prefers_chatgpt_over_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_path = _write_codex_auth(tmp_path)
    _host(monkeypatch, home=auth_path.parent, key="test-key", mode="auto")
    adapter = _adapter()

    adapter.validate()

    assert adapter.execution_metadata()["auth_mode"] == "chatgpt"
    assert adapter.credential_environment_sources() == {}
    assert adapter.credential_file_sources() == {"CODEX_AUTH_JSON": auth_path}


def test_auto_mode_falls_back_to_the_api_key_without_a_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _host(monkeypatch, home=tmp_path / ".codex", key="test-key")
    adapter = _adapter()

    adapter.validate()

    metadata = adapter.execution_metadata()
    assert (metadata["auth_mode"], metadata["auth_mode_requested"]) == ("api", "auto")
    assert metadata["auth_json_path"] is None
    assert adapter.credential_file_sources() == {}


def test_api_mode_uses_api_key_even_when_auth_file_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_path = _write_codex_auth(tmp_path)
    _host(monkeypatch, home=auth_path.parent, key="test-key", mode="api")
    adapter = _adapter()

    adapter.validate()

    assert adapter.execution_metadata()["auth_mode"] == "api"
    assert adapter.credential_environment_sources() == {"OPENAI_API_KEY": "test-key"}
    assert adapter.credential_file_sources() == {}


def test_api_mode_without_a_key_is_refused_even_with_a_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_path = _write_codex_auth(tmp_path)
    _host(monkeypatch, home=auth_path.parent, mode="api")

    with pytest.raises(OSError, match="requires OPENAI_API_KEY"):
        _adapter().validate()


def test_chatgpt_mode_requires_file_backed_auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _host(monkeypatch, home=tmp_path / ".codex", key="test-key", mode="chatgpt")

    with pytest.raises(OSError, match="file-backed credentials"):
        _adapter().validate()


@pytest.mark.parametrize(
    "text",
    ["not json", "{", "[]", "{}", "null"],
    ids=["text", "truncated", "list", "empty", "null"],
)
def test_an_unusable_auth_file_is_no_login(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str
) -> None:
    auth_path = _write_codex_auth(tmp_path, text)
    _host(monkeypatch, home=auth_path.parent, mode="chatgpt")

    with pytest.raises(OSError, match="file-backed credentials"):
        _adapter().validate()


def test_an_unknown_auth_mode_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch, key="test-key", mode="sometimes")

    with pytest.raises(ValueError, match=CODEX_AUTH_MODE_ENV):
        _adapter().validate()


def test_the_login_is_looked_for_in_the_users_home_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _host(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    auth_path = _write_codex_auth(tmp_path)
    adapter = _adapter()

    adapter.validate()

    assert adapter.credential_file_sources() == {"CODEX_AUTH_JSON": auth_path}


@pytest.mark.parametrize(
    ("model", "effort", "allowed"),
    [
        ("gpt-5.5", "xhigh", True),
        ("gpt-5.2", "high", True),
        ("gpt-5.2", "xhigh", False),
        ("gpt-5.4-mini", None, True),
        ("gpt-5.4-mini", "low", True),
        ("gpt-5.4-mini", "medium", False),
        ("gpt-6-luna", "low", True),
        ("gpt-6-luna", "medium", False),
        ("gpt-5.6-luna", "medium", False),
        ("gpt-5.6-terra", "medium", False),
    ],
)
def test_reasoning_effort_must_be_one_the_model_supports(
    monkeypatch: pytest.MonkeyPatch, model: str, effort: str | None, allowed: bool
) -> None:
    _host(monkeypatch, key="test-key")
    adapter = _adapter(model, effort)

    if allowed:
        adapter.validate()
        assert adapter.model_argument() == f"openai/{model}"
    else:
        with pytest.raises(ValueError, match="only supports reasoning levels"):
            adapter.validate()


def test_an_unsupported_model_is_refused_with_the_supported_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch, key="test-key")

    with pytest.raises(ValueError, match=r"only supports models: .*gpt-5\.4.*Received 'gpt-3'"):
        _adapter("gpt-3", None).validate()


def test_a_host_without_the_codex_cli_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _host(monkeypatch, key="test-key")
    monkeypatch.setenv("CODEX_CLI_PATH", str(tmp_path / "absent"))

    with pytest.raises(FileNotFoundError, match="CODEX_CLI_PATH"):
        _adapter().validate()


def test_model_summary_lists_every_supported_model_under_the_provider() -> None:
    summary = CodexCliAdapter.supported_model_summary().split(", ")

    assert summary == sorted(summary)
    assert all(item.startswith("openai/gpt-") for item in summary)


def test_the_harbor_cli_is_the_one_installed_next_to_the_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(executables.sys, "executable", str(tmp_path / "python"))
    with pytest.raises(FileNotFoundError, match="Harbor CLI not found"):
        executables.harbor_executable()

    harbor = tmp_path / "harbor"
    harbor.write_text("#!/bin/sh\n")
    with pytest.raises(FileNotFoundError, match="Harbor CLI not found"):
        executables.harbor_executable()  # present but not executable

    harbor.chmod(0o755)
    assert executables.harbor_executable() == str(harbor)
    assert os.access(harbor, os.X_OK)
