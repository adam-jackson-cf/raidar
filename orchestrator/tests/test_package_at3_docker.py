"""AT3: the package lifecycle's failure attribution in real containers (a ready Docker target only).

No model runs. ``PackageDriveAgent`` installs a fixture package with ``install/1`` and drives
``PackageCheckpoint`` in the trial's container, playing the agent's turns as UID 1000:

- a setup exiting 1 invalidates the trial (instrument);
- a junk stop result gets one repair turn, then closes as an agent outcome;
- a failing canary invalidates the trial before any agent turn;
- exhausted stop rounds end closed, as an agent outcome;
- planted test code exiting 126 under a passing gate canary is an agent outcome.

A healthy run is the control: nothing is invalid and the package finishes ``done``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from harbor.models.trial.config import AgentConfig

from tests.at3_support import OUTCOME, SCENARIOS, PackageDriveAgent
from tests.docker_trial_support import build_task, docker_target_ready, run_trial_sync

pytestmark = pytest.mark.skipif(
    not docker_target_ready(),
    reason="needs a ready Docker target (data check passing) and the built python pack image",
)

STEP = "checkpoint_1"


def drive(base: Path, scenario: str) -> dict[str, Any]:
    """One real trial with the scripted package driver; the outcome it recorded."""
    task = base / "task"
    build_task(task, {STEP: "Drive the packages"}, agent_timeout_sec=900)
    config = AgentConfig(import_path=PackageDriveAgent.import_path(), kwargs={"scenario": scenario})
    trial = run_trial_sync(task, base / "trials", config, f"at3-{scenario}")
    outcome: dict[str, Any] = json.loads(
        (trial / "steps" / STEP / "agent" / OUTCOME).read_text(encoding="utf-8")
    )
    assert set(SCENARIOS) >= {scenario}
    return outcome


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    return {name: drive(tmp_path_factory.mktemp(name), name) for name in SCENARIOS}


def test_a_healthy_package_is_installed_run_and_finishes_done(
    outcomes: dict[str, dict[str, Any]],
) -> None:
    outcome = outcomes["healthy"]
    assert outcome["invalid"] is None
    stop = outcome["record"]["stop/1"]["fault-probe"]
    assert stop["decisions"][-1]["action"] == "done"
    assert [run["passed"] for run in stop["canary"]] == [True]
    assert outcome["record"]["outputs/1"]["fault-probe"]["data"]["error"] is None


def test_a_setup_exiting_1_invalidates_the_trial(outcomes: dict[str, dict[str, Any]]) -> None:
    invalid = outcomes["setup_exit_1"]["invalid"]
    assert invalid is not None
    assert invalid["category"] == "instrument"
    assert "setup call failed" in invalid["message"]
    assert outcomes["setup_exit_1"]["resumes"] == []  # the agent never started


def test_a_failing_canary_invalidates_the_trial_before_the_agent_starts(
    outcomes: dict[str, dict[str, Any]],
) -> None:
    outcome = outcomes["canary_fails"]
    assert outcome["invalid"] is not None
    assert outcome["invalid"]["category"] == "instrument"
    assert "canary failed" in outcome["invalid"]["message"]
    assert outcome["resumes"] == []


def test_a_junk_stop_result_gets_one_repair_then_closes_as_an_agent_outcome(
    outcomes: dict[str, dict[str, Any]],
) -> None:
    outcome = outcomes["junk_stop"]
    assert outcome["invalid"] is None
    assert outcome["resumes"] == ["repair"]
    stop = outcome["record"]["stop/1"]["fault-probe"]
    assert stop["repaired"]
    assert stop["closed"]
    assert [d["action"] for d in stop["decisions"]] == ["repair", "closed"]
    assert stop["decisions"][0]["attribution"] == "agent_state"  # the canary passed again


def test_exhausted_rounds_end_closed(outcomes: dict[str, dict[str, Any]]) -> None:
    outcome = outcomes["rounds_exhausted"]
    assert outcome["invalid"] is None
    assert outcome["resumes"] == ["rework", "rework"]
    stop = outcome["record"]["stop/1"]["note-state"]
    assert stop["rounds"] == 2
    assert stop["closed"]
    assert stop["decisions"][-1]["reason"] == "resume beyond max_rounds"


def test_planted_test_code_exiting_126_under_a_passing_canary_is_an_agent_outcome(
    outcomes: dict[str, dict[str, Any]],
) -> None:
    outcome = outcomes["planted_exit_126"]
    assert outcome["invalid"] is None
    gate = outcome["record"]["gate/1"]
    assert gate["canary"]["verdict"] in ("passed", "refused")  # the starting workspace decided
    assert gate["gates"][-1]["verdict"] == "error"  # the planted conftest killed pytest


def test_planted_test_code_running_past_the_timeout_is_an_agent_outcome(
    outcomes: dict[str, dict[str, Any]],
) -> None:
    outcome = outcomes["planted_timeout"]
    assert outcome["invalid"] is None
    checks = outcome["record"]["gate/1"]["gates"][-1]["checks"]
    assert {c["cause"] for c in checks if c["status"] == "error"} == {"timeout"}
