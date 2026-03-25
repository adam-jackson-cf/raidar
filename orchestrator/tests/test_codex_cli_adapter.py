"""Tests for Codex CLI adapter behavior."""

from pathlib import Path

import pytest

from raidar.agents.adapters.codex_cli import CodexCliAdapter
from raidar.agents.config import AgentSpec, Harness, ModelTarget


def _config(model: str, provider: str = "codex") -> AgentSpec:
    return AgentSpec(
        harness=Harness.CODEX_CLI,
        model=ModelTarget(provider=provider, name=model),
    )


def test_registry_resolves_codex_adapter() -> None:
    adapter = _config("gpt-5.4-high").adapter()
    assert isinstance(adapter, CodexCliAdapter)


def test_validate_rejects_non_codex_provider() -> None:
    adapter = CodexCliAdapter(_config("gpt-5.4-high", provider="openai"))
    with pytest.raises(ValueError, match="provider 'codex'"):
        adapter.validate()


def test_validate_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_CLI_PATH", "/usr/local/bin/codex")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = CodexCliAdapter(_config("gpt-5.4-high"))
    with pytest.raises(OSError, match="require an API key"):
        adapter.validate()


def test_runtime_env_forwards_cli_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_CLI_PATH", "/usr/local/bin/codex")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    adapter = CodexCliAdapter(_config("gpt-5.4-high"))
    env = adapter.runtime_env()
    assert env["CODEX_CLI_PATH"] == "/usr/local/bin/codex"
    assert "OPENAI_API_KEY" not in env


@pytest.mark.parametrize(
    ("model_name", "model_argument", "reasoning_effort"),
    (
        ("gpt-5.2-low", "codex/gpt-5.2-codex", "low"),
        ("gpt-5.2-medium", "codex/gpt-5.2-codex", "medium"),
        ("gpt-5.2-high", "codex/gpt-5.2-codex", "high"),
        ("gpt-5.4-low", "codex/gpt-5.4", "low"),
        ("gpt-5.4-medium", "codex/gpt-5.4", "medium"),
        ("gpt-5.4-high", "codex/gpt-5.4", "high"),
        ("gpt-5.4-extra-high", "codex/gpt-5.4", "xhigh"),
        ("gpt-5.4-mini", "codex/gpt-5.4-mini", None),
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
    adapter = CodexCliAdapter(_config(model_name))
    adapter.validate()
    assert adapter.model_argument() == model_argument
    if reasoning_effort:
        assert list(adapter.extra_harbor_args()) == ["--ak", f"reasoning_effort={reasoning_effort}"]
    else:
        assert list(adapter.extra_harbor_args()) == []


def test_prepare_workspace_creates_codex_trace_dir(tmp_path: Path) -> None:
    adapter = CodexCliAdapter(_config("gpt-5.4-high"))
    adapter.prepare_workspace(tmp_path)
    assert (tmp_path / ".codex").exists()
