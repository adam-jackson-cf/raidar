import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from raidar.codex_auth import ResolvedCodexAuth
from raidar.schemas.scenario import LLMAsJudgeMetricConfig, ScenarioDefinition
from raidar.scorers import llm_as_judge

JUDGE_RESPONSE = """
{
  "passed": true,
  "score": 0.82,
  "verdict": "PASS WITH GAPS",
  "evidence": "Strong plan with one verification gap.",
  "findings": [
    {
      "id": "PJ1",
      "severity": "medium",
      "type": "verification-gap",
      "evidence": "Plan: run tests",
      "gap": "Retained benchmark evidence is not defined.",
      "why_it_matters": "The implementation could pass locally without comparable evidence.",
      "required_planning_change": "Name the benchmark artifact expected at completion."
    }
  ],
  "rubric_coverage": {
    "objective_fit": "pass",
    "outcome_verifiability": "gap"
  },
  "residual_risk": []
}
"""


def _scenario() -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        {
            "name": "judge-scenario",
            "scenario_revision": "v001",
            "description": "Judge a delivery output",
            "difficulty": "medium",
            "category": "greenfield-ui",
            "timeout_sec": 1800,
            "starter": {"root": "starter"},
            "requirements": {
                "items": [
                    {
                        "id": "req-copy",
                        "description": "Expected copy is present.",
                        "check": {
                            "type": "import_present",
                            "pattern": "Ready",
                            "description": "Expected copy exists",
                        },
                    }
                ]
            },
            "verification": {"gates": [], "required_commands": [], "min_quality_score": 0.0},
            "scorers": [{"id": "resource-efficiency", "version": 1, "weight": 1.0}],
            "prompt": {"entry": "prompt/task.md"},
        }
    )


def test_evaluate_llm_as_judge_metric_uses_judge_role_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    scenario_dir = tmp_path / "scenario"
    workspace = tmp_path / "workspace"
    judge_path = tmp_path / "scorer-definitions" / "judges" / "reviewer.toml"
    judge_path.parent.mkdir(parents=True)
    (scenario_dir / "prompt").mkdir(parents=True)
    (workspace / "src").mkdir(parents=True)
    judge_path.write_text(
        "You are a delivery reviewer.",
        encoding="utf-8",
    )
    (scenario_dir / "prompt" / "task.md").write_text("Build the page.", encoding="utf-8")
    (workspace / "src" / "page.tsx").write_text("export const Ready = true;\n", encoding="utf-8")

    calls: list[dict[str, str]] = []

    def fake_call_judge(*, judge_role: str, prompt: str) -> str:
        calls.append({"judge_role": judge_role, "prompt": prompt})
        return JUDGE_RESPONSE

    monkeypatch.setattr(llm_as_judge, "_call_judge", fake_call_judge)
    monkeypatch.setattr(
        llm_as_judge,
        "resolve_scorer_definition_file",
        lambda *args, **kwargs: judge_path,
    )

    metric = llm_as_judge.evaluate_llm_as_judge_metric(
        workspace=workspace,
        scenario_dir=scenario_dir,
        scenario=_scenario(),
        metric_id="plan-adherence",
        judge_path="judges/reviewer.toml",
    )

    assert metric.metric_id == "plan-adherence"
    assert metric.score == 0.82
    assert metric.passed is True
    assert metric.evidence == "Strong plan with one verification gap."
    assert metric.judge_output is not None
    assert metric.judge_output["verdict"] == "PASS WITH GAPS"
    assert metric.judge_output["findings"][0]["id"] == "PJ1"
    assert metric.judge_output["rubric_coverage"]["outcome_verifiability"] == "gap"
    assert len(calls) == 1
    assert calls[0]["judge_role"] == "You are a delivery reviewer."
    assert "src/page.tsx" in calls[0]["prompt"]
    assert "Structured judge inputs:" in calls[0]["prompt"]
    assert "changed_surfaces" in calls[0]["prompt"]
    assert "execution_outcomes" in calls[0]["prompt"]
    assert "deterministic_metric_summaries" in calls[0]["prompt"]


def test_llm_as_judge_redacts_secret_shaped_prompt_and_response(
    monkeypatch,
    tmp_path: Path,
) -> None:
    scenario_dir = tmp_path / "scenario"
    workspace = tmp_path / "workspace"
    judge_path = tmp_path / "scorer-definitions" / "judges" / "reviewer.toml"
    judge_path.parent.mkdir(parents=True)
    (scenario_dir / "prompt").mkdir(parents=True)
    (workspace / "src").mkdir(parents=True)
    judge_path.write_text("You are a delivery reviewer.", encoding="utf-8")
    (scenario_dir / "prompt" / "task.md").write_text(
        "Use token=super-secret-value-1234567890", encoding="utf-8"
    )
    (workspace / "src" / "page.tsx").write_text(
        "const password = 'hunter2-secret-value';\n", encoding="utf-8"
    )
    calls: list[dict[str, str]] = []

    def fake_call_judge(*, judge_role: str, prompt: str) -> str:
        calls.append({"judge_role": judge_role, "prompt": prompt})
        return json.dumps(
            {
                "passed": True,
                "score": 1,
                "evidence": "Bearer abcdefghijklmnopqrstuvwxyz1234567890",
                "findings": [{"evidence": "api_key=abcdef1234567890"}],
            }
        )

    monkeypatch.setattr(llm_as_judge, "_call_judge", fake_call_judge)
    monkeypatch.setattr(
        llm_as_judge,
        "resolve_scorer_definition_file",
        lambda *args, **kwargs: judge_path,
    )

    metric = llm_as_judge.evaluate_llm_as_judge_metric(
        workspace=workspace,
        scenario_dir=scenario_dir,
        scenario=_scenario(),
        metric_id="plan-adherence",
        judge_path="judges/reviewer.toml",
    )

    assert "super-secret-value" not in calls[0]["prompt"]
    assert "hunter2-secret-value" not in calls[0]["prompt"]
    assert "abcdefghijklmnopqrstuvwxyz" not in str(metric.evidence)
    assert "abcdef1234567890" not in str(metric.judge_output)


def test_call_codex_judge_uses_chatgpt_auth_without_api_key(
    monkeypatch,
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"tokens": {"id_token": "present"}}', encoding="utf-8")
    calls: list[dict[str, object]] = []

    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    monkeypatch.setattr(llm_as_judge.settings.llm_as_judge, "model", "gpt-5.5")
    monkeypatch.setattr(llm_as_judge.settings.llm_as_judge, "reasoning_effort", "low")
    monkeypatch.setattr(llm_as_judge.settings.llm_as_judge, "codex_auth_mode", "chatgpt")
    monkeypatch.setattr(
        llm_as_judge,
        "resolve_codex_auth",
        lambda *, requested_mode: ResolvedCodexAuth(
            requested_mode=requested_mode,
            resolved_mode="chatgpt",
            auth_json_path=auth_path,
            source_label="test auth",
        ),
    )

    def fake_run(*args, **kwargs):
        calls.append({"args": args, **kwargs})
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"item": {"type": "agent_message", "text": JUDGE_RESPONSE}}) + "\n",
            stderr="",
        )

    monkeypatch.setattr(llm_as_judge.subprocess, "run", fake_run)

    response = llm_as_judge._call_codex_judge(judge_role="judge", prompt="prompt")

    assert response == JUDGE_RESPONSE.strip()
    assert len(calls) == 1
    command = calls[0]["args"][0]
    assert command[:2] == ["codex", "exec"]
    assert "--json" in command
    assert command[command.index("--model") + 1] == "gpt-5.5"
    assert "-c" in command
    assert "model_reasoning_effort=low" in command
    assert command[-1] == "-"
    assert calls[0]["input"] == "judge\n\nprompt"
    env = calls[0]["env"]
    assert "CODEX_HOME" in env
    assert "OPENAI_API_KEY" not in env


def test_evaluate_llm_as_judge_metric_fails_when_judge_file_missing(tmp_path: Path) -> None:
    scenario_dir = tmp_path / "scenario"
    workspace = tmp_path / "workspace"
    scenario_dir.mkdir()
    workspace.mkdir()

    metric = llm_as_judge.evaluate_llm_as_judge_metric(
        workspace=workspace,
        scenario_dir=scenario_dir,
        scenario=_scenario(),
        metric_id="plan-adherence",
        judge_path="judges/missing.md",
    )

    assert metric.metric_id == "plan-adherence"
    assert metric.score == 0.0
    assert metric.passed is False
    assert metric.missing_patterns == ["judges/missing.md"]
    assert metric.judge_output is None


@pytest.mark.parametrize("judge_path", ["../outside.md", "/tmp/outside.md"])
def test_evaluate_llm_as_judge_metric_rejects_unsafe_judge_paths(
    judge_path: str,
    tmp_path: Path,
) -> None:
    scenario_dir = tmp_path / "scenario"
    workspace = tmp_path / "workspace"
    scenario_dir.mkdir()
    workspace.mkdir()

    metric = llm_as_judge.evaluate_llm_as_judge_metric(
        workspace=workspace,
        scenario_dir=scenario_dir,
        scenario=_scenario(),
        metric_id="plan-adherence",
        judge_path=judge_path,
    )

    assert metric.score == 0.0
    assert metric.passed is False
    assert metric.missing_patterns == [judge_path]
    assert "scorer definitions" in metric.evidence or "parent traversal" in metric.evidence


@pytest.mark.parametrize("judge_path", ["../outside.toml", "/tmp/outside.toml"])
def test_llm_judge_config_rejects_unsafe_scorer_paths(judge_path: str) -> None:
    with pytest.raises(ValidationError):
        LLMAsJudgeMetricConfig.model_validate({"judge": judge_path})
