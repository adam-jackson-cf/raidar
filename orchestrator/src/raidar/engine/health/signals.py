"""Run-health checks over the host-side evidence of agent phases: ``L``, ``P`` and ``T``.

``L1``/``L2`` read the liveness record, ``P2``/``P3`` the job manifest's concurrency and
comparison warnings, and ``T1``-``T3`` the tool-tree hashes, the sealed records manifest and
the reaper reports. See ``raidar.engine.health.audit`` for the full check list.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from raidar.contract.job_layout import TRIAL_RECORDS_DIR, TRIALS_DIR
from raidar.engine.health.base import Audit, make_check
from raidar.engine.health.liveness import LIVENESS_REPORT
from raidar.engine.health.model import PASS, UNMEASURED, VIOLATION, HealthCheck, HealthStatus
from raidar.engine.health.records_manifest import tamper_findings
from raidar.engine.health.tool_tree import TOOL_HASHES, load_tool_hashes
from raidar.engine.perf.contention import peak_during, trial_window
from raidar.engine.trial.reaper import load_reap_report
from raidar.grading.records import step_record_dir

_FINDINGS_SHOWN = 20


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text())
    except ValueError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _phases(a: Audit) -> list[dict[str, Any]] | None:
    report = _load_json(a.trial_dir / LIVENESS_REPORT)
    return None if report is None else [p for p in report.get("phases", []) if isinstance(p, dict)]


_NO_LIVENESS = f"no {LIVENESS_REPORT} (liveness off, or the monitor was not armed)"


def check_l1(a: Audit) -> HealthCheck:
    phases = _phases(a)
    if phases is None:
        return make_check("L1", UNMEASURED, _NO_LIVENESS)
    seen = {p.get("step") for p in phases}
    missing = [s.step for s in a.attempted if s.step not in seen]
    if missing:
        return make_check("L1", UNMEASURED, f"no liveness phase for {missing}", steps=missing)
    return make_check(
        "L1", PASS, f"liveness recorded for {len(phases)} agent phase(s)", phases=len(phases)
    )


def _blind(phase: dict[str, Any]) -> bool:
    return bool(phase.get("error")) or phase.get("sample_count", 0) == phase.get(
        "unmeasured_samples", 0
    )


def _finding(phase: dict[str, Any]) -> dict[str, Any]:
    keys = ("step", "decision", "reason", "first_idle_at_s")
    finding = {key: phase.get(key) for key in keys}
    if phase.get("decision") == "idle_killed":
        killed = (phase.get("kill") or {}).get("killed") or []
        finding["idle_for_s"] = phase.get("idle_for_s")
        finding["killed_pids"] = [str(line).split()[0] for line in killed if str(line).split()]
    return finding


def _l2_kill_failed(phases: list[dict[str, Any]]) -> HealthCheck | None:
    failed = [_finding(p) for p in phases if p.get("decision") == "kill_failed"]
    if not failed:
        return None
    steps = [str(f["step"]) for f in failed]
    return make_check(
        "L2", VIOLATION, f"the idle agent uid could not be killed in {steps}", steps=steps,
        findings=failed,
    )  # fmt: skip


def _l2_detail(observed: list[dict[str, Any]], killed: list[dict[str, Any]]) -> str:
    if killed:
        parts = [
            f"{f['step']}: idle {f['idle_for_s']} s, killed pids {f['killed_pids']}" for f in killed
        ]
        return "idle_killed (the step is verified and graded as after an agent timeout): " + (
            "; ".join(parts)
        )
    steps = [f["step"] for f in observed]
    return f"idle observed (not enforced) in {steps}" if observed else "agent active throughout"


def check_l2(a: Audit) -> HealthCheck:
    phases = _phases(a)
    if phases is None:
        return make_check("L2", UNMEASURED, _NO_LIVENESS)
    if (failed := _l2_kill_failed(phases)) is not None:
        return failed
    if not phases:
        return make_check("L2", UNMEASURED, "the liveness record holds no agent phase")
    return _l2_findings(phases)


def _l2_findings(phases: list[dict[str, Any]]) -> HealthCheck:
    blind = [p.get("step") for p in phases if _blind(p)]
    observed = [_finding(p) for p in phases if p.get("decision") == "idle_observed"]
    killed = [_finding(p) for p in phases if p.get("decision") == "idle_killed"]
    if blind:
        detail = f"every liveness sample was unmeasured in {blind}"
        return make_check("L2", UNMEASURED, detail, steps=blind, findings=observed + killed)
    return make_check("L2", PASS, _l2_detail(observed, killed), findings=observed + killed)


def _own_peak(a: Audit) -> int | None:
    try:
        trials = sorted(p for p in (a.job_dir / TRIALS_DIR).glob("*") if p.is_dir())
        windows = [w for p in trials if (w := trial_window(str(a.job_dir), p.name, p)) is not None]
        mine = trial_window(str(a.job_dir), a.trial_dir.name, a.trial_dir)
    except ValueError:
        return None
    return peak_during(mine, windows) if mine is not None and mine in windows else None


def check_p2(a: Audit) -> HealthCheck:
    target = a.job.get("execution_target")
    effective = target.get("effective_concurrency") if isinstance(target, Mapping) else None
    configured = a.job.get("concurrency")
    if configured is None and effective is None:
        return make_check("P2", UNMEASURED, "job.json records no concurrency")
    peak = _own_peak(a)
    return make_check(
        "P2",
        PASS,
        f"configured {configured}, effective {effective}, peak trials during this one {peak}",
        configured=configured,
        effective=effective,
        peak_concurrent_trials=peak,
    )


def _warnings(a: Audit) -> list[str]:
    runner = a.job.get("runner") or {}
    target = a.job.get("execution_target") or {}
    admission = a.job.get("admission") or {}
    warnings = []
    if runner.get("dirty"):
        warnings.append("the Raidar runner tree was dirty")
    if isinstance(target, Mapping) and target.get("docker") is None:
        warnings.append("no Docker daemon facts recorded for the target")
    if admission.get("overridden"):
        warnings.append("ran unsmoked under ALLOW_UNSMOKED")
    if a.record.agent_override:
        warnings.append(
            f"agent override {a.record.agent_override!r}: not comparable with real arms"
        )
    return warnings


def check_p3(a: Audit) -> HealthCheck:
    if "runner" not in a.job:
        return make_check("P3", UNMEASURED, "job.json records no runner provenance")
    warnings = _warnings(a)
    detail = "comparison warnings: " + "; ".join(warnings) if warnings else "no comparison warnings"
    return make_check("P3", PASS, detail, warnings=warnings)


def _tool_unchanged(
    later: list[dict[str, Any]], key: tuple[Any, Any]
) -> tuple[HealthStatus, str, dict[str, Any]]:
    unmeasured = [p.get("step") for p in later if not p.get("measured")]
    if not later or unmeasured:
        return UNMEASURED, f"no complete post-step hashes (unmeasured: {unmeasured or 'all'})", {}
    state = "tool tree" if key[0] else "no tool tree provisioned; none appeared"
    return PASS, f"{state} unchanged across {len(later)} post-step hash(es)", {"sha256": key[1]}


def _baseline(points: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((p for p in points if p.get("point") == "baseline" and p.get("measured")), None)


def _differs(point: dict[str, Any], key: tuple[Any, Any]) -> bool:
    return bool(point.get("measured")) and (point.get("present"), point.get("sha256")) != key


def tool_status(points: list[dict[str, Any]]) -> tuple[HealthStatus, str, dict[str, Any]]:
    baseline = _baseline(points)
    if baseline is None:
        return UNMEASURED, "no measured baseline hash of the tool tree", {}
    later = [p for p in points if p.get("point") != "baseline"]
    key = (baseline.get("present"), baseline.get("sha256"))
    changed = [p for p in later if _differs(p, key)]
    if not changed:
        return _tool_unchanged(later, key)
    steps = [str(p.get("step")) for p in changed]
    detail = f"tool tree changed after the baseline hash, at {steps}"
    return VIOLATION, detail, {"steps": steps, "baseline": key[1]}


def check_t1(a: Audit) -> HealthCheck:
    hashes = load_tool_hashes(a.trial_dir)
    if hashes is None:
        return make_check("T1", UNMEASURED, f"no {TOOL_HASHES} (no hashes were taken)")
    status, detail, evidence = tool_status(
        [p for p in hashes.get("points", []) if isinstance(p, dict)]
    )
    return make_check("T1", status, detail, **evidence)


def check_t2(a: Audit) -> HealthCheck:
    trial_id = a.record.trial.trial_id
    expected = (a.job.get("records_manifests") or {}).get(trial_id)
    if expected is None:
        sealed_later = "finished_at" not in a.job
        why = (
            "sealed after this audit; verify with `make health`"
            if sealed_later
            else "job.json holds no digest for this trial"
        )
        return make_check("T2", UNMEASURED, f"records manifest not verifiable: {why}")
    record_file = a.job_dir / TRIAL_RECORDS_DIR / f"{trial_id}.json"
    findings = tamper_findings(a.trial_dir, record_file, a.job_dir, expected)
    if findings:
        return make_check(
            "T2",
            VIOLATION,
            "; ".join(findings[:_FINDINGS_SHOWN]),
            differences=findings[:_FINDINGS_SHOWN],
        )
    return make_check("T2", PASS, "records manifest verifies")


def _reap_problem(a: Audit, step: str) -> str | None:
    report = load_reap_report(step_record_dir(a.trial_dir, step))
    if report is None:
        return "no agent-reap.json"
    if report.get("error"):
        return f"reap error: {report['error']}"
    return f"{len(report['residual'])} residual process(es)" if report.get("residual") else None


def check_t3(a: Audit) -> HealthCheck:
    problems = {s.step: _reap_problem(a, s.step) for s in a.attempted}
    residual = {k: v for k, v in problems.items() if v and not v.startswith("no ")}
    if residual:
        return make_check("T3", VIOLATION, f"reaper residuals: {residual}", steps=list(residual))
    absent = [k for k, v in problems.items() if v]
    if absent or not problems:
        return make_check(
            "T3", UNMEASURED, f"no reap report for {absent or 'any step'}", steps=absent
        )
    return make_check("T3", PASS, f"no residual agent process after {len(problems)} step(s)")
