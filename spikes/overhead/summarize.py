"""Aggregate overhead reps into p50/p95 per phase per cell (plan task 0.4).

Reads ``_work/results/<cell>/rep-*.json`` and writes ``baseline-summary.json``
(tracked evidence) plus a Markdown table on stdout. Percentiles use linear
interpolation between order statistics (numpy's default); with n=10 the p95
sits between the 9th and 10th values, so it is close to the max.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "_work" / "results"
CELLS = ("cold", "warm", "warm-artifact", "warm-init")

# (key, label, scope, budgeted)
PHASES = [
    (
        "environment_start",
        "Environment start (Harbor `environment_setup`: build/cache check + up)",
        "trial",
        True,
    ),
    ("oracle_image_build", "Oracle image build (spike-managed; cold only)", "trial", True),
    (
        "component_apply",
        "Component apply [STUB: bundle upload + root install into harness baseline]",
        "trial",
        True,
    ),
    (
        "per_step_reset",
        "Per-step reset (Harbor workdir upload + `setup.sh` + healthcheck)",
        "step",
        True,
    ),
    ("setup_sh", "  of which `setup.sh` reset itself", "step", False),
    ("artifact_collection", "Artifact collection (`/app` -> host, Harbor)", "step", True),
    ("snapshot_transfer", "Snapshot transfer (verifier: download or artifact copy)", "step", True),
    (
        "verifier_plumbing",
        "Verifier plumbing (verifier total - oracle run - snapshot transfer)",
        "step",
        True,
    ),
    ("step_archive_gap", "Step archive gap (step outputs -> `steps/<k>/`)", "step", True),
    (
        "scorer_orchestration",
        "Scorer orchestration [STUB: 1 no-op `--network none` container + host digest/write]",
        "step",
        True,
    ),
    ("teardown_result_write", "Teardown + Harbor `result.json` (compose down)", "trial", True),
    ("raidar_result_write", "Raidar result write [STUB: rep JSON write]", "trial", True),
    (
        "framework_overhead_trial",
        "Framework overhead per 2-step trial (total - agent - oracle + scorer stub)",
        "trial",
        False,
    ),
    ("agent_execution", "(excluded) agent execution: reference agent", "step", False),
    ("oracle_run", "(excluded) oracle run: SCB tests in oracle container", "step", False),
]


def pct(values: list[float], q: float) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    pos = (len(vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def samples(rep: dict) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {key: [] for key, *_ in PHASES}
    harbor = rep["harbor_timing_info"]
    raidar = rep["raidar_phases"]
    out["environment_start"].append(harbor["environment_setup_s"])
    if rep["cell"] == "cold":
        out["oracle_image_build"].append(rep["oracle_image"]["build_s"])
    comp = rep.get("component_apply_stub") or {}
    if comp:
        out["component_apply"].append(comp["total_s"])
    scorer = {s["step"]: s for s in rep["scorer_orchestration_stub"]["steps"]}
    agent_total = oracle_total = 0.0
    for step in raidar["steps"]:
        out["per_step_reset"].append(step["step_prepare_s"])
        out["setup_sh"].append(step["per_step_reset_setup_sh_s"])
        out["artifact_collection"].append(step["artifact_collection_s"])
        out["snapshot_transfer"].append(step["snapshot_transfer_s"])
        out["verifier_plumbing"].append(
            step["verifier_plumbing_s"] - (step["snapshot_transfer_s"] or 0.0)
        )
        if step["step_archive_gap_s"] is not None:
            out["step_archive_gap"].append(step["step_archive_gap_s"])
        sc = scorer.get(step["step"])
        if sc:
            out["scorer_orchestration"].append(sc["container_s"] + sc["host_s"])
        out["agent_execution"].append(step["agent_execution_s"])
        out["oracle_run"].append(step["oracle_run_s"])
        agent_total += step["agent_execution_s"] or 0.0
        oracle_total += step["oracle_run_s"] or 0.0
    out["teardown_result_write"].append(raidar["trial_teardown_and_result_write_s"])
    out["raidar_result_write"].append(rep.get("raidar_result_write_s"))
    out["framework_overhead_trial"].append(
        harbor["trial_total_s"]
        - agent_total
        - oracle_total
        + rep["scorer_orchestration_stub"]["total_s"]
    )
    return out


def main() -> None:
    summary: dict = {"method": "linear-interpolated percentiles; seconds", "cells": {}}
    for cell in CELLS:
        reps = [json.loads(p.read_text()) for p in sorted((RESULTS / cell).glob("rep-*.json"))]
        reps = [r for r in reps if r.get("rep", 0) >= 1]  # rep 0 = harness smoke
        if not reps:
            continue
        failed = [r["rep"] for r in reps if r.get("exception")]
        merged: dict[str, list[float]] = {key: [] for key, *_ in PHASES}
        for rep in reps:
            for key, vals in samples(rep).items():
                merged[key].extend(v for v in vals if v is not None)
        summary["cells"][cell] = {
            "reps": len(reps),
            "failed_reps": failed,
            "rewards_all_pass": all(
                all((v or {}).get("strict_pass_rate") == 1.0 for v in r["rewards"].values())
                for r in reps
            ),
            "running_containers_at_start": [r.get("running_containers_at_start") for r in reps],
            "phases": {
                key: {
                    "n": len(merged[key]),
                    "p50": pct(merged[key], 0.5),
                    "p95": pct(merged[key], 0.95),
                    "min": min(merged[key]) if merged[key] else None,
                    "max": max(merged[key]) if merged[key] else None,
                }
                for key, *_ in PHASES
            },
        }
    (HERE / "baseline-summary.json").write_text(json.dumps(summary, indent=2))

    cells = list(summary["cells"])
    header = "| Phase | Scope | " + " | ".join(f"{c} p50 / p95 (n)" for c in cells) + " |"
    print(header)
    print("|" + "---|" * (2 + len(cells)))
    for key, label, scope, _ in PHASES:
        cols = []
        for c in cells:
            ph = summary["cells"][c]["phases"][key]
            if not ph["n"]:
                cols.append("—")
            else:
                cols.append(f"{ph['p50']:.2f} / {ph['p95']:.2f} ({ph['n']})")
        print(f"| {label} | {scope} | " + " | ".join(cols) + " |")
    for c in cells:
        info = summary["cells"][c]
        print(
            f"\n{c}: reps={info['reps']} failed={info['failed_reps']} "
            f"all_pass={info['rewards_all_pass']} load={info['running_containers_at_start']}"
        )


if __name__ == "__main__":
    main()
