"""Post-trial evidence: Harbor TimingInfo + Raidar phase timings + A' checks.

Pure functions over a finished trial directory and its ``TrialResult`` JSON so
task 0.4 can re-derive timings for any trial without rerunning it.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from raidar_spike.verifier import tree_digest


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _span(timing: dict | None) -> float | None:
    if not timing:
        return None
    start, end = _ts(timing.get("started_at")), _ts(timing.get("finished_at"))
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def _diff(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None:
        return None
    return (later - earlier).total_seconds()


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def timings(result: dict, trial_dir: Path, hook_events: list[dict]) -> dict[str, Any]:
    """Harbor TimingInfo phases plus Raidar-attributed per-step phases (seconds)."""
    harbor = {
        "trial_total_s": _diff(_ts(result.get("finished_at")), _ts(result.get("started_at"))),
        "environment_setup_s": _span(result.get("environment_setup")),
        "agent_setup_s": _span(result.get("agent_setup")),
        "steps": [],
    }
    raidar_steps = []
    previous_end = _ts((result.get("agent_setup") or {}).get("finished_at"))
    steps = result.get("step_results") or []
    for index, step in enumerate(steps):
        name = step["step_name"]
        agent = step.get("agent_execution") or {}
        verifier = step.get("verifier") or {}
        a_start, a_end = _ts(agent.get("started_at")), _ts(agent.get("finished_at"))
        v_start, v_end = _ts(verifier.get("started_at")), _ts(verifier.get("finished_at"))
        harbor["steps"].append(
            {"step": name, "agent_execution_s": _span(agent), "verifier_s": _span(verifier)}
        )
        step_dir = trial_dir / "steps" / name
        reset = _load(step_dir / "agent" / "raidar-reset.json") or {}
        vrep = _load(step_dir / "verifier" / "raidar_verifier.json") or {}
        vt = vrep.get("timings", {})
        verifier_total = _span(verifier)
        oracle_run = vt.get("oracle_run_s")
        next_start = None
        if index + 1 < len(steps):
            next_start = _ts((steps[index + 1].get("agent_execution") or {}).get("started_at"))
        raidar_steps.append(
            {
                "step": name,
                # Harbor: agent-log reset + workdir upload + setup.sh + healthcheck (+ agent probe
                # runs inside agent_execution for the spike agents).
                "step_prepare_s": _diff(a_start, previous_end),
                "per_step_reset_setup_sh_s": (reset.get("duration_ms") or 0) / 1000
                if reset
                else None,
                "agent_execution_s": _span(agent),
                # Harbor: agent log upload + /app artifact collection to host.
                "artifact_collection_s": _diff(v_start, a_end),
                "verifier_total_s": verifier_total,
                "oracle_leak_check_s": vt.get("leak_check_s"),
                "snapshot_transfer_s": vt.get("snapshot_transfer_s"),
                "oracle_stage_s": vt.get("stage_s"),
                "oracle_run_s": oracle_run,
                "oracle_result_write_s": vt.get("result_write_s"),
                # Spike-only assertion (leak scan) is excluded: it is not a production phase.
                "verifier_plumbing_s": (
                    verifier_total - oracle_run - (vt.get("leak_check_s") or 0.0)
                    if verifier_total is not None and oracle_run is not None
                    else None
                ),
                # Harbor: move step outputs into steps/<name>/ (and next step boundary).
                "step_archive_gap_s": _diff(next_start, v_end) if next_start else None,
            }
        )
        previous_end = v_end or a_end
    finished = _ts(result.get("finished_at"))
    return {
        "harbor_timing_info": harbor,
        "raidar_phases": {
            "steps": raidar_steps,
            "trial_teardown_and_result_write_s": _diff(finished, previous_end),
        },
        "hook_events": hook_events,
    }


def checks(result: dict, trial_dir: Path, manifest: dict, plant_step: str | None) -> dict[str, Any]:
    steps = result.get("step_results") or []
    names = [s["step_name"] for s in steps]
    rewards = {s["step_name"]: ((s.get("verifier_result") or {}).get("rewards")) for s in steps}

    # --- check 1: host-side verifier, separate container, no tests in agent env
    c1_steps = {}
    for name in names:
        vrep = _load(trial_dir / "steps" / name / "verifier" / "raidar_verifier.json") or {}
        leak = vrep.get("leak_check", {})
        container = vrep.get("oracle_container", {})
        c1_steps[name] = {
            "verifier_report_present": bool(vrep),
            "leak_clean": leak.get("clean"),
            "hidden_test_files_found": leak.get("hidden_test_files_found"),
            "tests_dir_entries": leak.get("tests_dir_entries"),
            "oracle_token_hits": leak.get("oracle_token_hits"),
            "separate_container": container.get("container_name"),
            "oracle_container_rc": container.get("return_code"),
            "rewards": vrep.get("rewards"),
        }
    c1_ok = (
        manifest["static_isolation"]["clean"]
        and bool(c1_steps)
        and all(
            v["verifier_report_present"]
            and v["leak_clean"]
            and v["separate_container"]
            and v["rewards"]
            for v in c1_steps.values()
        )
    )

    # --- check 2: context isolation (probe at every step after the plant step)
    c2_steps = {}
    plant_report = None
    if plant_step and plant_step in names:
        plant_report = _load(trial_dir / "steps" / plant_step / "agent" / "raidar-plant.json")
    after_plant = names[names.index(plant_step) + 1 :] if plant_step in names else []
    for name in after_plant:
        probe = _load(trial_dir / "steps" / name / "agent" / "raidar-probe.json") or {}
        reset = _load(trial_dir / "steps" / name / "agent" / "raidar-reset.json") or {}
        killed = [k.get("args", "") for k in reset.get("killed", [])]
        c2_steps[name] = {
            "probe_present": bool(probe),
            "reset_present": bool(reset),
            "tokens_searched": len(probe.get("searched_tokens", [])),
            "token_hits": {k: v for k, v in (probe.get("token_hits") or {}).items() if v},
            "planted_process_alive": probe.get("planted_process_alive"),
            "harness_state_entries": probe.get("harness_state_entries"),
            "reset_killed": killed,
            "reset_residual": reset.get("residual"),
            "reset_state": reset.get("state"),
            "reset_duration_ms": reset.get("duration_ms"),
        }
    first_after = after_plant[0] if after_plant else None
    c2_ok = bool(
        plant_report
        and plant_report.get("planted_process")
        and first_after
        and all(
            v["probe_present"]
            and v["reset_present"]
            and v["tokens_searched"] > 0
            and not v["token_hits"]
            and not v["planted_process_alive"]
            and not v["harness_state_entries"]
            and not v["reset_residual"]
            and not any(s.get("reset_failed") for s in v["reset_state"] or [])
            for v in c2_steps.values()
        )
        and any("raidar-planted-sentinel" in k for k in c2_steps[first_after]["reset_killed"])
    )

    # --- check 3: per-step /app artifacts on host == graded snapshot
    c3_steps = {}
    for name in names:
        app_dir = trial_dir / "steps" / name / "artifacts" / "app"
        vrep = _load(trial_dir / "steps" / name / "verifier" / "raidar_verifier.json") or {}
        digest = tree_digest(app_dir) if app_dir.is_dir() else None
        c3_steps[name] = {
            "artifact_dir": str(app_dir),
            "exists": app_dir.is_dir(),
            "files": sum(1 for p in app_dir.rglob("*") if p.is_file()) if app_dir.is_dir() else 0,
            "digest_matches_graded_snapshot": digest is not None
            and digest == vrep.get("snapshot_digest"),
        }
    c3_ok = bool(c3_steps) and all(
        v["exists"] and v["digest_matches_graded_snapshot"] for v in c3_steps.values()
    )

    return {
        "steps_executed": names,
        "steps_declared": manifest["steps"],
        "rewards": rewards,
        "trial_reward": (result.get("verifier_result") or {}).get("rewards"),
        "exception": (result.get("exception_info") or {}).get("exception_message"),
        "step_exceptions": {
            s["step_name"]: (s.get("exception_info") or {}).get("exception_message")
            for s in steps
            if s.get("exception_info")
        },
        "check1_host_verifier_no_tests_in_env": {"ok": c1_ok, "steps": c1_steps},
        "check2_context_isolation": {"ok": c2_ok, "plant": plant_report, "steps": c2_steps},
        "check3_artifacts_on_host": {"ok": c3_ok, "steps": c3_steps},
    }
