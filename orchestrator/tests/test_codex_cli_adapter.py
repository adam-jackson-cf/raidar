"""Tests for Codex CLI adapter behavior."""

import json
from pathlib import Path

import pytest

from raidar.agents.adapters.codex_cli import CodexCliAdapter
from raidar.agents.adapters.factory import resolve_adapter
from raidar.agents.config import AgentSpec, Harness, ModelTarget
from raidar.codex_auth import CODEX_AUTH_MODE_ENV

CODEX_HARBOR_AGENT = "raidar.agents.harbor_agents.cli_agents:CodexCliHarborAgent"


def _config(
    model: str,
    provider: str = "openai",
    reasoning_effort: str | None = None,
) -> AgentSpec:
    return AgentSpec(
        harness=Harness.CODEX_CLI,
        model=ModelTarget(provider=provider, name=model, reasoning_effort=reasoning_effort),
    )


def _write_codex_auth(tmp_path: Path) -> Path:
    auth_dir = tmp_path / ".codex"
    auth_dir.mkdir()
    auth_path = auth_dir / "auth.json"
    auth_path.write_text(json.dumps({"id_token": "token"}), encoding="utf-8")
    return auth_path


def test_registry_resolves_codex_adapter() -> None:
    adapter = resolve_adapter(_config("gpt-5.4", reasoning_effort="high"))
    assert isinstance(adapter, CodexCliAdapter)


def test_validate_rejects_non_codex_provider() -> None:
    adapter = CodexCliAdapter(_config("gpt-5.4", provider="codex", reasoning_effort="high"))
    with pytest.raises(ValueError, match="provider 'openai'"):
        adapter.validate()


def test_validate_requires_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODEX_CLI_PATH", "/usr/local/bin/codex")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(CODEX_AUTH_MODE_ENV, raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    adapter = CodexCliAdapter(_config("gpt-5.4", reasoning_effort="high"))
    with pytest.raises(OSError, match="make codex-auth-setup"):
        adapter.validate()


def test_runtime_env_forwards_cli_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_CLI_PATH", "/usr/local/bin/codex")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    adapter = CodexCliAdapter(_config("gpt-5.4", reasoning_effort="high"))
    env = adapter.runtime_env()
    assert env["CODEX_CLI_PATH"] == "/usr/local/bin/codex"
    assert "OPENAI_API_KEY" not in env


def test_validate_accepts_file_backed_chatgpt_auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_path = _write_codex_auth(tmp_path)
    monkeypatch.setenv("CODEX_CLI_PATH", "/usr/local/bin/codex")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(auth_path.parent))
    adapter = CodexCliAdapter(_config("gpt-5.4", reasoning_effort="high"))

    adapter.validate()

    assert adapter.execution_metadata()["auth_mode"] == "chatgpt"
    assert adapter.harbor_harness_import_path() == CODEX_HARBOR_AGENT
    assert adapter.local_secret_files() == {"CODEX_AUTH_JSON": auth_path}
    assert "PYTHONPATH" in adapter.runtime_env()


def test_auto_mode_prefers_chatgpt_over_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_path = _write_codex_auth(tmp_path)
    monkeypatch.setenv("CODEX_CLI_PATH", "/usr/local/bin/codex")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CODEX_HOME", str(auth_path.parent))
    monkeypatch.setenv(CODEX_AUTH_MODE_ENV, "auto")
    adapter = CodexCliAdapter(_config("gpt-5.4", reasoning_effort="high"))

    adapter.validate()

    assert adapter.execution_metadata()["auth_mode"] == "chatgpt"
    assert adapter.harbor_harness_import_path() == CODEX_HARBOR_AGENT
    assert adapter.excluded_run_env_keys() == {"OPENAI_API_KEY"}
    assert adapter.local_secret_files() == {"CODEX_AUTH_JSON": auth_path}


def test_api_mode_uses_api_key_even_when_auth_file_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_path = _write_codex_auth(tmp_path)
    monkeypatch.setenv("CODEX_CLI_PATH", "/usr/local/bin/codex")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CODEX_HOME", str(auth_path.parent))
    monkeypatch.setenv(CODEX_AUTH_MODE_ENV, "api")
    adapter = CodexCliAdapter(_config("gpt-5.4", reasoning_effort="high"))

    adapter.validate()

    assert adapter.execution_metadata()["auth_mode"] == "api"
    assert adapter.harbor_harness_import_path() == CODEX_HARBOR_AGENT
    assert adapter.excluded_run_env_keys() == set()
    assert adapter.local_secret_files() == {}


def test_chatgpt_mode_requires_file_backed_auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CODEX_CLI_PATH", "/usr/local/bin/codex")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv(CODEX_AUTH_MODE_ENV, "chatgpt")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    adapter = CodexCliAdapter(_config("gpt-5.4", reasoning_effort="high"))

    with pytest.raises(OSError, match="file-backed credentials"):
        adapter.validate()


@pytest.mark.parametrize(
    ("model_name", "model_argument", "reasoning_effort"),
    (
        ("gpt-5.5", "openai/gpt-5.5", "low"),
        ("gpt-5.5", "openai/gpt-5.5", "medium"),
        ("gpt-5.5", "openai/gpt-5.5", "high"),
        ("gpt-5.5", "openai/gpt-5.5", "xhigh"),
        ("gpt-5.3-codex-spark", "openai/gpt-5.3-codex-spark", "low"),
        ("gpt-5.3-codex-spark", "openai/gpt-5.3-codex-spark", "medium"),
        ("gpt-5.3-codex-spark", "openai/gpt-5.3-codex-spark", "high"),
        ("gpt-5.3-codex-spark", "openai/gpt-5.3-codex-spark", "xhigh"),
        ("gpt-5.2", "openai/gpt-5.2", "low"),
        ("gpt-5.2", "openai/gpt-5.2", "medium"),
        ("gpt-5.2", "openai/gpt-5.2", "high"),
        ("gpt-5.4", "openai/gpt-5.4", "low"),
        ("gpt-5.4", "openai/gpt-5.4", "medium"),
        ("gpt-5.4", "openai/gpt-5.4", "high"),
        ("gpt-5.4", "openai/gpt-5.4", "xhigh"),
        ("gpt-5.4-mini", "openai/gpt-5.4-mini", None),
        ("gpt-5.4-mini", "openai/gpt-5.4-mini", "low"),
    ),
)
def test_aliases_requested_codex_models(
    monkeypatch: pytest.MonkeyPatch,
    model_name: str,
    model_argument: str,
    reasoning_effort: str | None,
) -> None:
    monkeypatch.setenv("CODEX_CLI_PATH", "/usr/local/bin/codex")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    adapter = CodexCliAdapter(_config(model_name, reasoning_effort=reasoning_effort))
    adapter.validate()
    assert adapter.model_argument() == model_argument
    if reasoning_effort:
        assert list(adapter.extra_harbor_args()) == ["--ak", f"reasoning_effort={reasoning_effort}"]
    else:
        assert list(adapter.extra_harbor_args()) == []


def test_prepare_workspace_creates_codex_trace_dir(tmp_path: Path) -> None:
    adapter = CodexCliAdapter(_config("gpt-5.4", reasoning_effort="high"))
    adapter.prepare_workspace(tmp_path)
    assert (tmp_path / ".codex").exists()
