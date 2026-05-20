from pathlib import Path

import pytest
from pydantic import ValidationError

from raidar.schemas.scenario import LLMAsJudgeMetricConfig, ScenarioDefinition
from raidar.scoring import llm_as_judge


class _Message:
    content = """
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


class _Choice:
    message = _Message()


class _Response:
    choices = [_Choice()]


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
            "acceptance": {
                "requirements": [
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

    calls: list[dict[str, object]] = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return _Response()

    monkeypatch.setattr(llm_as_judge, "completion", fake_completion)
    monkeypatch.setattr(
        llm_as_judge,
        "resolve_scorer_definition_file",
        lambda *args, **kwargs: judge_path,
    )

    metric = llm_as_judge.evaluate_llm_as_judge_metric(
        workspace=workspace,
        scenario_dir=scenario_dir,
        scenario=_scenario(),
        metric_id="plan-quality",
        judge_path="judges/reviewer.toml",
    )

    assert metric.metric_id == "plan-quality"
    assert metric.score == 0.82
    assert metric.passed is True
    assert metric.evidence == "Strong plan with one verification gap."
    assert metric.judge_output is not None
    assert metric.judge_output["verdict"] == "PASS WITH GAPS"
    assert metric.judge_output["findings"][0]["id"] == "PJ1"
    assert metric.judge_output["rubric_coverage"]["outcome_verifiability"] == "gap"
    assert calls
    assert calls[0]["messages"][0]["content"] == "You are a delivery reviewer."
    assert "src/page.tsx" in calls[0]["messages"][1]["content"]


def test_evaluate_llm_as_judge_metric_fails_when_judge_file_missing(tmp_path: Path) -> None:
    scenario_dir = tmp_path / "scenario"
    workspace = tmp_path / "workspace"
    scenario_dir.mkdir()
    workspace.mkdir()

    metric = llm_as_judge.evaluate_llm_as_judge_metric(
        workspace=workspace,
        scenario_dir=scenario_dir,
        scenario=_scenario(),
        metric_id="plan-quality",
        judge_path="judges/missing.md",
    )

    assert metric.metric_id == "plan-quality"
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
        metric_id="plan-quality",
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
