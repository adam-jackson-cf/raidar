"""Summarise one spike arm: ``collect.py <out-dir> [--arm NAME]`` writes ``<out-dir>/summary.json``.

Per concurrency dir it reads the fixture's job directory: every trial's verifier step records
(snapshot digest, grading case ids/statuses and a ledger digest, leak scans), the per-step
``raidar-reset.json`` files, the grader copy timings (``grading.log`` / ``coverage.log``),
and ``perf-check.json``; plus the container mounts the watcher captured.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

COPY_LINE = re.compile(r"\[raidar-copy\] (.*)$", re.MULTILINE)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def load(path: Path) -> object | None:
    return json.loads(path.read_text()) if path.is_file() else None


def copy_timings(log: Path) -> dict[str, float] | None:
    if not log.is_file():
        return None
    found = COPY_LINE.findall(log.read_text(errors="replace"))
    if not found:
        return None
    return {
        k: float(v) for k, v in (kv.split("=") for kv in found[-1].split() if "=" in kv)
    }


def leak(report: object) -> object:
    if not isinstance(report, dict):
        return None
    return {
        "clean": report.get("clean"),
        "hidden_copies": report.get("hidden_copies", []),
    }


def step_summary(trial: Path, record_file: Path) -> dict[str, object]:
    record = json.loads(record_file.read_text())
    step = record["step"]
    step_dir = record_file.parent
    evidence = load(step_dir / "evidence.json") or {}
    grading = (evidence.get("grading") or {}) if isinstance(evidence, dict) else {}
    ledger = ((grading.get("evidence") or {}).get("ledger")) if grading else None
    cases = [[c["id"], c["status"]] for c in record["grade"]["cases"]]
    resets = sorted(
        str(p.relative_to(trial))
        for p in trial.rglob("raidar-reset.json")
        if step in p.parts
    )
    return {
        "snapshot_digest": record["snapshot"]["digest"],
        "snapshot_used": record["snapshot"]["used"],
        "grading_cases": cases,
        "grading_ledger_digest": digest(ledger) if ledger is not None else None,
        "infra_failure": record["grade"].get("infra_failure"),
        "leak": leak(record.get("leak_check")),
        "pre_agent_leak": leak(record.get("pre_agent_leak_check")),
        "reset_report_paths": resets,
        "has_reset_report": bool(resets),
        "grader_copy": {
            "grading": copy_timings(step_dir / "grading.log"),
            "coverage": copy_timings(step_dir / "coverage.log"),
        },
        "timings": record.get("timings", {}),
    }


def grader_copy_stats(trials: dict[str, object]) -> dict[str, dict[str, float]]:
    """p50/max of the grader's copy-in / run / copy-out seconds over every graded step run."""
    values: dict[str, list[float]] = {}
    for steps in trials.values():
        for step in steps.values():  # type: ignore[attr-defined]
            for role, timings in step["grader_copy"].items():
                for key, value in (timings or {}).items():
                    values.setdefault(f"{role}.{key}", []).append(value)
    return {
        key: {
            "n": len(v),
            "p50": sorted(v)[len(v) // 2],
            "max": max(v),
        }
        for key, v in sorted(values.items())
    }


def concurrency_summary(path: Path) -> dict[str, object]:
    trials: dict[str, object] = {}
    for job in sorted(path.glob("jobs/*/perf-fixture")):
        for trial in sorted((job / "trials").glob("*")):
            steps = {}
            for record_file in sorted(trial.glob("raidar-oracle/steps/*/record.json")):
                steps[record_file.parent.name] = step_summary(trial, record_file)
            trials[trial.name] = steps
    report = load(path / "perf-check.json")
    checks = report.get("checks", []) if isinstance(report, dict) else []
    phases = {
        f"{c['phase']}[{c['condition']}]" if c.get("condition") else c["phase"]: {
            key: c.get(key)
            for key in ("samples", "p50_s", "p95_s", "budget_s", "verdict")
        }
        for c in checks
    }
    exit_file = Path(f"{path}.exit")
    return {
        "pytest_exit": int(exit_file.read_text()) if exit_file.is_file() else None,
        "trials": trials,
        "perf_phases": phases,
        "grader_copy_seconds": grader_copy_stats(trials),
        "perf_check": report,  # kept whole
    }


def mounts(out: Path) -> dict[str, object]:
    file = out / "mounts.jsonl"
    records = (
        [json.loads(x) for x in file.read_text().splitlines() if x]
        if file.is_file()
        else []
    )
    binds = [
        {"container": r["name"], **m}
        for r in records
        for m in r["mounts"]
        if m["Type"] == "bind"
    ]
    return {
        "containers_seen": len(records),
        "bind_sources": binds,
        "containers": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--arm", default=None)
    args = parser.parse_args()
    summary = {
        "arm": args.arm or args.out.name,
        "concurrency": {
            d.name.removeprefix("concurrency-"): concurrency_summary(d)
            for d in sorted(args.out.glob("concurrency-*"))
            if d.is_dir()
        },
        "mounts": mounts(args.out),
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(f"wrote {args.out / 'summary.json'}")


if __name__ == "__main__":
    main()
