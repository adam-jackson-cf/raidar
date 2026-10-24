"""Credential preflight: every selected arm's agent has its host credential before any trial."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raidar.design.experiment.loader import load_experiment
from raidar.engine.job.credentials import (
    CredentialPreflightError,
    credential_issues,
    harness_credential_issue,
    preflight_credentials,
)
from raidar.engine.trial.plan import PlanOptions
from raidar.subject.agent.config import Harness
from tests.experiment_spec_support import AGENTS_BENCHMARK

FAKE = "x" * 24  # placeholder credential value; never a real secret


def _codex_home(tmp_path: Path, *, with_auth: bool) -> dict[str, str]:
    home = tmp_path / ("codex-session" if with_auth else "codex-empty")
    home.mkdir()
    if with_auth:
        (home / "auth.json").write_text(json.dumps({"tokens": {"id": "placeholder"}}))
    return {"CODEX_HOME": str(home)}


def test_codex_accepts_session_file_or_api_key_per_auth_mode(tmp_path: Path) -> None:
    session = _codex_home(tmp_path, with_auth=True)
    empty = _codex_home(tmp_path, with_auth=False)
    assert harness_credential_issue(Harness.CODEX_CLI, session) is None
    assert harness_credential_issue(Harness.CODEX_CLI, {**empty, "OPENAI_API_KEY": FAKE}) is None
    assert "not configured" in str(harness_credential_issue(Harness.CODEX_CLI, empty))
    api_only = {**session, "CODEX_AUTH_MODE": "api"}
    assert "OPENAI_API_KEY" in str(harness_credential_issue(Harness.CODEX_CLI, api_only))
    chatgpt = {**empty, "CODEX_AUTH_MODE": "chatgpt", "OPENAI_API_KEY": FAKE}
    assert "file-backed" in str(harness_credential_issue(Harness.CODEX_CLI, chatgpt))
    bogus = {**session, "CODEX_AUTH_MODE": "bogus"}
    assert "CODEX_AUTH_MODE" in str(harness_credential_issue(Harness.CODEX_CLI, bogus))


@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"])
def test_claude_code_accepts_an_api_key_or_an_oauth_token(name: str) -> None:
    assert harness_credential_issue(Harness.CLAUDE_CODE, {name: FAKE}) is None


def test_claude_code_without_credentials_or_with_forced_oauth_and_no_token_fails() -> None:
    missing = harness_credential_issue(Harness.CLAUDE_CODE, {"ANTHROPIC_API_KEY": "  "})
    assert missing is not None
    assert "CLAUDE_CODE_OAUTH_TOKEN" in missing
    forced = {"ANTHROPIC_API_KEY": FAKE, "CLAUDE_FORCE_OAUTH": "1"}
    assert "CLAUDE_FORCE_OAUTH" in str(harness_credential_issue(Harness.CLAUDE_CODE, forced))


def test_issues_name_the_agent_field_of_each_selected_arm_only(tmp_path: Path) -> None:
    spec = load_experiment(AGENTS_BENCHMARK).spec
    env = _codex_home(tmp_path, with_auth=False)
    issues = credential_issues(spec, ["codex", "claude"], env)
    fields = [issue.field for issue in issues]
    assert fields == [
        "agents.0.name ('codex', codex, arm 'codex')",
        "agents.1.name ('claude', claude-code, arm 'claude')",
    ]
    only_claude = credential_issues(spec, ["claude"], {**env, "ANTHROPIC_API_KEY": FAKE})
    assert only_claude == []
    rendered = issues[1].render()
    assert rendered.startswith("agents.1.name")
    assert FAKE not in rendered


class _Plan:
    """The slice of ExperimentPlan the preflight reads."""

    def __init__(self, arms: list[str], override: str | None = None) -> None:
        self.loaded = load_experiment(AGENTS_BENCHMARK)
        self.options = PlanOptions(agent_override=override)  # type: ignore[arg-type]
        self._arms = arms

    def selected_trials(self) -> list[object]:
        return [type("T", (), {"arm": arm})() for arm in self._arms]


def test_preflight_fails_before_any_trial_without_echoing_values(tmp_path: Path) -> None:
    env = {**_codex_home(tmp_path, with_auth=False), "OPENAI_API_KEY": FAKE}
    with pytest.raises(CredentialPreflightError) as caught:
        preflight_credentials(_Plan(["codex", "claude"]), env)  # type: ignore[arg-type]
    message = str(caught.value)
    assert "no trial was started" in message
    assert "agents.1.name" in message
    assert "agents.0" not in message
    assert FAKE not in message
    preflight_credentials(_Plan(["codex"]), env)  # type: ignore[arg-type]
    preflight_credentials(_Plan(["claude"], override="nop"), {})  # type: ignore[arg-type]
