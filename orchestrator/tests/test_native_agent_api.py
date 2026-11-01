"""The native agent's public API: host credentials and reset state, by harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from raidar_contract.agent import AgentDefinitionError

from raidar.subject.agent.config import AgentSpec, Harness, ModelTarget
from raidar.subject.agent.definition import harness_of, load_agent, provider_of
from raidar.subject.agent.native import (
    adapter_class_for_harness,
    credentials,
    resolve_adapter,
)
from tests.capability_support import CAPABILITIES

KEY = "test-key"


def _session_home(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "auth.json").write_text('{"tokens": {"access_token": "x"}}')
    return {"CODEX_HOME": str(tmp_path)}


def test_codex_authenticates_from_a_session_file_and_reports_where_it_is(tmp_path: Path) -> None:
    found = credentials(Harness.CODEX_CLI, _session_home(tmp_path))

    assert found.issue is None
    assert found.session_file == tmp_path / "auth.json"
    assert found.session_present
    assert found.session_used
    assert found.mode_env == "CODEX_AUTH_MODE"


def test_codex_api_mode_does_not_use_the_session_file_that_exists(tmp_path: Path) -> None:
    env = {**_session_home(tmp_path), "CODEX_AUTH_MODE": "api", "OPENAI_API_KEY": KEY}

    found = credentials(Harness.CODEX_CLI, env)

    assert found.issue is None
    assert found.session_present
    assert not found.session_used


def test_codex_without_any_login_says_why_and_keeps_the_expected_location(tmp_path: Path) -> None:
    found = credentials(Harness.CODEX_CLI, {"CODEX_HOME": str(tmp_path)})

    assert "not configured" in str(found.issue)
    assert found.session_file == tmp_path / "auth.json"
    assert not found.session_present


def test_claude_has_credentials_but_no_session_file() -> None:
    assert credentials(Harness.CLAUDE_CODE, {"ANTHROPIC_API_KEY": KEY}).session_file is None
    missing = credentials(Harness.CLAUDE_CODE, {})
    assert "CLAUDE_CODE_OAUTH_TOKEN" in str(missing.issue)
    forced = credentials(
        Harness.CLAUDE_CODE, {"ANTHROPIC_API_KEY": KEY, "CLAUDE_FORCE_OAUTH": "yes"}
    )
    assert "CLAUDE_FORCE_OAUTH" in str(forced.issue)


def test_a_codex_login_in_the_default_home_is_found_without_codex_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "auth.json").write_text('{"tokens": {"access_token": "x"}}')

    found = credentials(Harness.CODEX_CLI, {})

    assert found.issue is None
    assert found.session_file == tmp_path / ".codex" / "auth.json"
    assert found.session_used


def test_codex_chatgpt_mode_without_a_session_file_is_refused_not_silently_keyed(
    tmp_path: Path,
) -> None:
    env = {"CODEX_HOME": str(tmp_path), "CODEX_AUTH_MODE": "chatgpt", "OPENAI_API_KEY": KEY}

    found = credentials(Harness.CODEX_CLI, env)

    assert "file-backed" in str(found.issue)
    assert not found.session_used


def test_a_harness_without_a_registered_adapter_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("raidar.subject.agent.adapters.registry.registry._adapters", {})
    spec = AgentSpec(harness=Harness.CODEX_CLI, model=ModelTarget(provider="openai", name="x"))

    with pytest.raises(ValueError, match="No adapter registered for harness codex-cli"):
        resolve_adapter(spec)
    with pytest.raises(ValueError, match="No adapter registered for harness codex-cli"):
        adapter_class_for_harness(Harness.CODEX_CLI)


@pytest.mark.parametrize("invalid", [None, "model", "reasoning"])
def test_declared_models_must_all_be_runnable_by_the_registered_adapter(
    tmp_path: Path, invalid: str | None
) -> None:
    models = {
        "first": {"canonical": "gpt-5.4", "reasoning": ["low", "high"]},
        "second": {"canonical": "gpt-5.3-codex-spark", "reasoning": ["low"]},
    }
    if invalid == "model":
        models["second"]["canonical"] = "gpt-nonexistent"
    elif invalid == "reasoning":
        models["second"]["reasoning"] = ["ultra"]
    document = {
        "schema": 1,
        "name": "fixture",
        "adapter": "codex",
        "tool": {"package": "test-cli", "binary": "test", "version": "1.0"},
        "settings": {"isolated_home": True, "ignore_user_config": True},
        "placement": {"skills": "{home}/skills", "instructions": "{home}/AGENTS.md"},
        "resume": True,
        "models": models,
        "capabilities": CAPABILITIES,
    }
    (tmp_path / "fixture.yaml").write_text(json.dumps(document))
    if invalid:
        with pytest.raises(AgentDefinitionError, match=r"models\.second"):
            load_agent(tmp_path, "fixture")
    else:
        definition = load_agent(tmp_path, "fixture")
        assert harness_of(definition) == Harness.CODEX_CLI
        assert provider_of(definition) == "openai"
