"""Credential preflight: every selected arm's agent has its host credential before any trial."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raidar.design.experiment.schema import ExperimentSpec
from raidar.engine.job.credentials import (
    CredentialPreflightError,
    credential_issues,
    harness_credential_issue,
    preflight_credentials,
)
from raidar.engine.trial.agent_config import AgentOverride
from raidar.engine.trial.plan import PlanOptions
from raidar.subject.agent.config import Harness
from tests.experiment_spec_support import experiment_document

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


def _spec() -> ExperimentSpec:
    """Two codex agents (ids ``luna`` and ``second``), one arm each."""
    document = experiment_document(kind="benchmark")
    document["agents"].append({**document["agents"][0], "id": "second"})
    document["arms"] = [
        {"id": "first", "agent": "luna", "workflow": "raidar/one-shot@2"},
        {"id": "second", "agent": "second", "workflow": "raidar/one-shot@2"},
    ]
    document["analysis"]["baseline_arm"] = "first"
    return ExperimentSpec.model_validate(document)


class _Plan:
    """The parsed agent/arm inputs the preflight consumes, without a run directory."""

    def __init__(self, arms: list[str], override: AgentOverride | None = None) -> None:
        spec = _spec()
        self.loaded = SimpleNamespace(spec=spec)
        agents = {
            entry.id: SimpleNamespace(
                entry=entry, harness=Harness.CODEX_CLI, released=SimpleNamespace(ref=entry.release)
            )
            for entry in spec.agents
        }
        self.arms = {
            arm.id: SimpleNamespace(arm=SimpleNamespace(agent=agents[arm.agent]))
            for arm in spec.arms
        }
        self.options = PlanOptions(agent_override=override)
        self._arms = arms

    def selected_trials(self) -> list[object]:
        return [SimpleNamespace(arm=arm) for arm in self._arms]


def test_issues_name_the_agent_field_of_each_selected_arm_only(tmp_path: Path) -> None:
    plan = _Plan(["first", "second"])
    env = _codex_home(tmp_path, with_auth=False)
    issues = credential_issues(plan, ["first", "second"], env)  # type: ignore[arg-type]
    assert [issue.field.split(" ")[0] for issue in issues] == [
        "agents.0.release",
        "agents.1.release",
    ]
    only_second = credential_issues(plan, ["second"], env)  # type: ignore[arg-type]
    assert [issue.field.split(" ")[0] for issue in only_second] == ["agents.1.release"]
    with_key = {**env, "OPENAI_API_KEY": FAKE}
    assert credential_issues(plan, ["first", "second"], with_key) == []  # type: ignore[arg-type]
    rendered = issues[1].render()
    assert rendered.startswith("agents.1.release")
    assert FAKE not in rendered


def test_an_agent_shared_by_several_selected_arms_is_reported_once(tmp_path: Path) -> None:
    plan = _Plan(["first"])
    plan.arms["third"] = plan.arms["first"]  # a second arm running the same agent
    env = _codex_home(tmp_path, with_auth=False)

    issues = credential_issues(plan, ["first", "third"], env)  # type: ignore[arg-type]

    assert [issue.field.split(" ")[0] for issue in issues] == ["agents.0.release"]


def test_preflight_fails_before_any_trial_without_echoing_values(tmp_path: Path) -> None:
    empty = _codex_home(tmp_path, with_auth=False)
    plan = _Plan(["first", "second"])
    leaky = {**empty, "CODEX_AUTH_MODE": "chatgpt", "OPENAI_API_KEY": FAKE}
    with pytest.raises(CredentialPreflightError) as caught:
        preflight_credentials(plan, leaky)  # type: ignore[arg-type]
    message = str(caught.value)
    assert "no trial was started" in message
    assert "agents.0.release" in message
    assert "agents.1.release" in message
    assert FAKE not in message
    preflight_credentials(plan, {**empty, "OPENAI_API_KEY": FAKE})  # type: ignore[arg-type]


def test_a_no_llm_agent_override_needs_no_credential(tmp_path: Path) -> None:
    override = _Plan(["first"], override="nop")

    preflight_credentials(override, {})  # type: ignore[arg-type]


def test_preflight_reads_the_process_environment_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan: Any = _Plan(["first"])
    monkeypatch.setenv("CODEX_HOME", str(_codex_home(tmp_path, with_auth=False)["CODEX_HOME"]))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_AUTH_MODE", raising=False)
    with pytest.raises(CredentialPreflightError, match="agents.0.release"):
        preflight_credentials(plan)

    monkeypatch.setenv("OPENAI_API_KEY", FAKE)
    preflight_credentials(plan)
