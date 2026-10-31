"""The native agent's public API: host credentials and reset state, by harness."""

from __future__ import annotations

from pathlib import Path

from raidar_contract.agent_records import StatePath

from raidar.engine.health.setup_script import render_setup_script
from raidar.subject.agent.config import Harness
from raidar.subject.agent.native import credentials, native_agent_class, state_paths

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


def test_state_paths_cover_both_harnesses_codex_first() -> None:
    paths = state_paths()

    assert [p.baseline for p in paths] == [
        "codex-home",
        "codex-home",
        "agents-home",
        "claude-config",
        "claude-config",
        "claude.json",
    ]
    assert StatePath("$HOME_DIR/.claude.json", "claude.json", "file") in paths


def test_the_reset_script_resets_every_native_agents_state_path() -> None:
    script = render_setup_script()

    assert "@STATE_TARGETS@" not in script
    for path in state_paths():
        assert f'  "{path.target}|{path.baseline}|{path.kind}"' in script


def test_every_harness_resolves_a_native_agent_class_with_the_protocol_classmethods() -> None:
    for harness in Harness:
        native = native_agent_class(harness)
        assert callable(native.credentials)  # type: ignore[attr-defined]
        assert native.state_paths()  # type: ignore[attr-defined]
