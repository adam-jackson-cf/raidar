"""Behavior tests for persisted warm orchestration budget validation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = REPO_ROOT / "scripts" / "validate-runtime-stack-smoke.py"


def _write_run(tmp_path: Path, *, overhead: float) -> tuple[Path, Path]:
    scenario = tmp_path / "scenario.yaml"
    scenario.write_text("name: demo\nscenario_revision: v001\n", encoding="utf-8")
    experiments = tmp_path / "experiments"
    run_path = experiments / "benchmarks" / "demo" / "runs" / "run-01" / "run.json"
    run_path.parent.mkdir(parents=True)
    run_path.write_text(
        json.dumps(
            {
                "id": "run-01",
                "config": {
                    "scenario_name": "demo",
                    "scenario_revision": "v001",
                    "harness": "codex-cli",
                    "model": "openai/gpt-5.6-terra",
                },
                "scores": {
                    "metadata": {
                        "harbor": {
                            "time_to_experiment_start_sec": 1.25,
                            "orchestration_overhead_excluding_agent_and_test_sec": overhead,
                            "cache": {
                                "contract": {"id": "contract", "hash": "hash"},
                                "image_key": "image-key",
                                "image": {"hit": True},
                            },
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return scenario, experiments


def _run_validator(scenario: Path, experiments: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--scenario",
            str(scenario),
            "--harness",
            "codex-cli",
            "--provider",
            "openai",
            "--model",
            "gpt-5.6-terra",
            "--experiments-root",
            str(experiments),
            "--experiment-kind",
            "benchmark",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_runtime_stack_validator_accepts_warm_sub_ten_second_overhead(tmp_path: Path) -> None:
    scenario, experiments = _write_run(tmp_path, overhead=4.5)

    result = _run_validator(scenario, experiments)

    assert result.returncode == 0, result.stderr
    assert "orchestration_overhead_excluding_agent_and_test_sec=4.5" in result.stdout


def test_runtime_stack_validator_rejects_slow_non_test_orchestration(tmp_path: Path) -> None:
    scenario, experiments = _write_run(tmp_path, overhead=10.0)

    result = _run_validator(scenario, experiments)

    assert result.returncode != 0
    assert "orchestration_overhead_excluding_agent_and_test_sec 10.0 >= 10.0" in result.stderr
