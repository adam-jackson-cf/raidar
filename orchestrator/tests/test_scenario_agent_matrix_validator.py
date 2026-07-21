"""Behavior tests for persisted scenario AgentSpec matrix validation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "validate-scenario-agent-matrix.py"


@pytest.fixture(scope="module")
def validator_module():
    spec = importlib.util.spec_from_file_location("scenario_agent_matrix_validator", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Could not load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def matrix_job():
    core_metric = SimpleNamespace(id="functional", type="core")
    judge_metric = SimpleNamespace(id="requirements-adherence", type="llm-as-judge")
    scorer_ref = SimpleNamespace(id="demo-scorer")
    resolved_scorer = SimpleNamespace(
        id="demo-scorer",
        metrics=[core_metric, judge_metric],
    )
    return SimpleNamespace(
        scenario=SimpleNamespace(
            name="demo",
            scenario_revision="v001",
            scorers=[scorer_ref],
            resolved_metrics=lambda: [core_metric, judge_metric],
            resolved_scorers=lambda: [resolved_scorer],
        ),
        agent=SimpleNamespace(
            harness="codex-cli",
            provider="openai",
            model="gpt-5.6-terra",
            reasoning_effort="low",
        ),
    )


def _config() -> dict[str, object]:
    return {
        "scenario_name": "demo",
        "scenario_revision": "v001",
        "harness": "codex-cli",
        "model": "openai/gpt-5.6-terra",
        "reasoning_effort": "low",
    }


def test_artifact_config_requires_exact_provider_qualified_agent_spec(
    validator_module, matrix_job
) -> None:
    assert validator_module._summary_matches({"config": _config()}, matrix_job) is True
    validator_module._validate_run_config(Path("run.json"), {"config": _config()}, matrix_job)

    wrong_provider = _config()
    wrong_provider["model"] = "anthropic/gpt-5.6-terra"

    assert validator_module._summary_matches({"config": wrong_provider}, matrix_job) is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scenario_name", "other"),
        ("scenario_revision", "v002"),
        ("harness", "claude-code"),
        ("model", "anthropic/gpt-5.6-terra"),
        ("model", "openai/gpt-5.5"),
        ("reasoning_effort", "medium"),
    ],
)
def test_nested_run_config_rejects_agent_spec_mismatches(
    validator_module,
    matrix_job,
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    config = _config()
    config[field] = value

    with pytest.raises(ValueError, match="AgentSpec config mismatch"):
        validator_module._validate_run_config(tmp_path / "run.json", {"config": config}, matrix_job)


def test_warm_timing_accepts_explicit_overhead_and_rejects_budget_breach(
    validator_module, tmp_path: Path
) -> None:
    run = {
        "scores": {
            "metadata": {
                "harbor": {
                    "time_to_experiment_start_sec": 2.0,
                    "orchestration_overhead_excluding_agent_and_test_sec": 9.5,
                    "cache": {
                        "baseline": {"hit": True},
                        "preflight": {"hit": True},
                        "image": {"hit": True},
                    },
                }
            }
        }
    }
    run_path = tmp_path / "run.json"

    assert validator_module._warm_timings(run_path, run, require_warm=True) == (
        2.0,
        9.5,
    )

    run["scores"]["metadata"]["harbor"]["orchestration_overhead_excluding_agent_and_test_sec"] = (
        10.0
    )
    with pytest.raises(ValueError, match="Warm orchestration budget exceeded"):
        validator_module._warm_timings(run_path, run, require_warm=True)

    del run["scores"]["metadata"]["harbor"]["orchestration_overhead_excluding_agent_and_test_sec"]
    with pytest.raises(ValueError, match="Orchestration timings missing"):
        validator_module._warm_timings(run_path, run, require_warm=True)


def _analysis_run() -> dict[str, object]:
    judge_output = {
        "passed": True,
        "score": 0.8,
        "verdict": "PASS WITH GAPS",
        "evidence": "Requirement intent is substantially satisfied.",
        "findings": [],
        "rubric_coverage": {"intent": "pass"},
        "residual_risk": [],
    }
    return {
        "terminated_early": False,
        "scores": {
            "unscored": False,
            "metric_scores": [
                {
                    "metric_id": "functional",
                    "evidence": "required_commands=2/2",
                    "judge_output": None,
                },
                {
                    "metric_id": "requirements-adherence",
                    "evidence": judge_output["evidence"],
                    "judge_output": judge_output,
                },
            ],
            "scorer_results": [
                {
                    "scorer_id": "demo-scorer",
                    "metric_contributions": [
                        {"metric_id": "functional"},
                        {"metric_id": "requirements-adherence"},
                    ],
                }
            ],
        },
    }


def test_meaningful_analysis_requires_exact_metrics_and_structured_judge_output(
    validator_module, matrix_job, tmp_path: Path
) -> None:
    run_path = tmp_path / "run.json"
    run = _analysis_run()

    assert validator_module._meaningful_scorer_analysis(run_path, run, matrix_job) == (
        ("functional", "requirements-adherence"),
        ("demo-scorer",),
    )

    run["scores"]["metric_scores"][1]["judge_output"] = None
    with pytest.raises(ValueError, match="invalid structured output"):
        validator_module._meaningful_scorer_analysis(run_path, run, matrix_job)


def test_meaningful_analysis_rejects_fabricated_or_incomplete_results(
    validator_module, matrix_job, tmp_path: Path
) -> None:
    run_path = tmp_path / "run.json"
    run = _analysis_run()
    run["scores"]["metric_scores"][0]["evidence"] = (
        "Selected scorer did not emit metric: functional"
    )
    with pytest.raises(ValueError, match="failed analysis"):
        validator_module._meaningful_scorer_analysis(run_path, run, matrix_job)

    run = _analysis_run()
    run["scores"]["metric_scores"].pop()
    with pytest.raises(ValueError, match="Metric result mismatch"):
        validator_module._meaningful_scorer_analysis(run_path, run, matrix_job)
