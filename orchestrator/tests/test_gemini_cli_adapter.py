"""Tests for Gemini adapter behavior."""

from pathlib import Path

import pytest

from agentic_eval.harness.adapters.gemini_cli import GeminiCliAdapter
from agentic_eval.harness.config import Agent, HarnessConfig, ModelTarget


def _config(model: str, provider: str = "google") -> HarnessConfig:
    return HarnessConfig(
        agent=Agent.GEMINI,
        model=ModelTarget(provider=provider, name=model),
    )


def test_registry_resolves_gemini_adapter():
    adapter = _config("gemini-3-pro-preview").adapter()
    assert isinstance(adapter, GeminiCliAdapter)


def test_harbor_agent_name_is_gemini_cli():
    adapter = GeminiCliAdapter(_config("gemini-3-pro-preview"))
    assert adapter.harbor_agent() == "gemini-cli"


def test_validate_rejects_non_google_provider():
    adapter = GeminiCliAdapter(_config("gemini-3-pro-preview", provider="vertex"))
    with pytest.raises(ValueError, match="provider 'google'"):
        adapter.validate()


def test_validate_rejects_unsupported_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_CLI_PATH", "/usr/local/bin/gemini")
    adapter = GeminiCliAdapter(_config("gemini-2.0-flash"))
    with pytest.raises(ValueError, match="only supports models"):
        adapter.validate()


def test_validate_requires_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_CLI_PATH", "/usr/local/bin/gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    adapter = GeminiCliAdapter(_config("gemini-3-pro-preview"))
    with pytest.raises(OSError, match="require an API key"):
        adapter.validate()


@pytest.mark.parametrize(
    "model_name",
    ("gemini-3-pro-preview", "gemini-3-flash-preview"),
)
def test_validate_accepts_supported_models(monkeypatch: pytest.MonkeyPatch, model_name: str):
    monkeypatch.setenv("GEMINI_CLI_PATH", "/usr/local/bin/gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    adapter = GeminiCliAdapter(_config(model_name))
    adapter.validate()


def test_runtime_env_forwards_cli_only(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_CLI_PATH", "/usr/local/bin/gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    adapter = GeminiCliAdapter(_config("gemini-3-pro-preview"))
    env = adapter.runtime_env()
    assert env["GEMINI_CLI_PATH"] == "/usr/local/bin/gemini"
    assert "GEMINI_API_KEY" not in env


def test_prepare_workspace_creates_gemini_session_dir(tmp_path: Path):
    adapter = GeminiCliAdapter(_config("gemini-3-pro-preview"))
    adapter.prepare_workspace(tmp_path)
    assert (tmp_path / ".gemini").exists()
