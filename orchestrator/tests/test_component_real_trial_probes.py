"""Discovery probes in real Harbor trials (plan WS-C C2, C4 acceptance).

Costs real LLM calls, so it runs only when ``RAIDAR_REAL_LLM_PROBES=1``:

    RAIDAR_REAL_LLM_PROBES=1 RAIDAR_PROBE_EVIDENCE_DIR=<dir> \\
      uv run --project orchestrator --extra dev pytest \\
      orchestrator/tests/test_component_real_trial_probes.py

Model policy (ADR-001 §2.7): codex-cli + gpt-6-luna at reasoning low only. The
plan's acceptance names Claude Code; under this policy the real probe runs are
Codex and the Claude Code mapping is covered by unit tests. Trials are batched:
one trial carries every harness/workspace/prompt built-in, one carries the
review loop.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from harbor.models.trial.config import AgentConfig

from raidar.agents.config import Harness
from raidar.codex_auth import resolve_codex_auth
from raidar.components import CompiledCondition
from tests.component_support import PILOT_DIR, all_builtin_uses, compile_uses, use
from tests.component_trial_support import (
    build_task,
    orbstack_available,
    pack_image_present,
    run_trial_sync,
)

MODEL = "openai/gpt-6-luna"
REASONING_EFFORT = "low"
CODEX_VERSION = "0.156.1"
INSTRUCTION = (
    "Create a file hello.py in the current directory whose main block prints exactly "
    "'hello'. Keep it minimal and stop when done."
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RAIDAR_REAL_LLM_PROBES") != "1"
    or not (orbstack_available() and pack_image_present()),
    reason="real-LLM probe trials run only with RAIDAR_REAL_LLM_PROBES=1 on OrbStack",
)


def _evidence_dir(tmp_path: Path) -> Path:
    configured = os.environ.get("RAIDAR_PROBE_EVIDENCE_DIR")
    return Path(configured) if configured else tmp_path


def _codex_agent(compiled: CompiledCondition) -> AgentConfig:
    auth = resolve_codex_auth(requested_mode="chatgpt")
    if auth.auth_json_path is None:
        pytest.fail("host Codex OAuth/session auth.json is required")
    os.environ["CODEX_AUTH_JSON_PATH"] = str(auth.auth_json_path)
    base = AgentConfig(
        name="codex",
        model_name=MODEL,
        kwargs={"version": CODEX_VERSION, "reasoning_effort": REASONING_EFFORT},
    )
    return compiled.apply_to_agent_config(base)


def _run_and_probe(tmp_path: Path, compiled: CompiledCondition, name: str) -> dict:
    root = _evidence_dir(tmp_path) / name
    task = root / "task"
    build_task(task, compiled, {"checkpoint_1": INSTRUCTION}, agent_timeout_sec=900)
    trial = run_trial_sync(task, root / "trials", _codex_agent(compiled), name)
    report = compiled.probe_step(trial / "steps" / "checkpoint_1" / "agent", 0).to_json()
    result = json.loads((trial / "result.json").read_text())
    agent_result = result["step_results"][0]["agent_result"] or {}
    summary = {
        "trial_dir": str(trial),
        "compiled_digest": compiled.digest,
        "probe_report": report,
        "cost_usd": agent_result.get("cost_usd"),
        "n_input_tokens": agent_result.get("n_input_tokens"),
        "exception": result["step_results"][0].get("exception_info"),
    }
    (root / "probe-summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary


def test_every_harness_workspace_and_prompt_builtin_is_discovered_on_codex(
    tmp_path: Path,
) -> None:
    compiled = compile_uses(*all_builtin_uses(Harness.CODEX_CLI))

    summary = _run_and_probe(tmp_path, compiled, "builtins-codex")

    failures = [r for r in summary["probe_report"]["results"] if not r["passed"]]
    assert summary["probe_report"]["passed"], failures


def test_review_loop_runs_reviewer_sessions_billed_to_the_arm(tmp_path: Path) -> None:
    compiled = compile_uses(
        use("review-loop@1", max_review_rounds=1),
        use("rules-file@1", ("components/guided/AGENTS.md",)),
        base_dir=PILOT_DIR,
    )

    summary = _run_and_probe(tmp_path, compiled, "review-loop-codex")

    assert summary["probe_report"]["passed"], summary["probe_report"]
    trial = Path(summary["trial_dir"]) / "steps" / "checkpoint_1" / "agent"
    record = json.loads((trial / "review-loop" / "review-loop.json").read_text())
    stage_costs = [stage["cost_usd"] for stage in record["stages"]]
    assert [stage["stage"] for stage in record["stages"]][:2] == ["implement", "review"]
    assert None not in stage_costs
    assert summary["cost_usd"] == pytest.approx(sum(stage_costs))
    trajectory = json.loads((trial / "trajectory.json").read_text())
    assert {sub["trajectory_id"] for sub in trajectory["subagent_trajectories"]} >= {
        "stage_1_implement",
        "stage_2_review",
    }
