"""Smoke-before-measure admission (mechanics-v1 design r2 §3.2; fxl ``cohort_smoke``).

``raidar run`` of any experiment kind except ``smoke`` is refused unless the jobs root holds a
clean smoke job of the same setup: the same Raidar toolchain digest, runtime pack recipe,
every arm's agent name/model/effort and the same execution target (name and Docker facts).
*Clean* means the job finished, every selected trial has a valid record, every trial's
``health.json`` exists with no violation, and in every trial every step's agent changed
``/app`` (the step's recorded snapshot digest differs from its parent's; the first step's parent
is the empty tree), so a smoke where the agent only asked a question cannot clear a run.
``--allow-unsmoked`` (``ALLOW_UNSMOKED=1``) admits without one; ``job.json`` records the
decision under ``admission``. Smoke kinds are always admitted.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from raidar.contract.job_layout import JOB_LOCK_FILE, JOB_MANIFEST, TRIAL_RECORDS_DIR
from raidar.engine.health.model import load_health
from raidar.engine.trial.models import TrialRecord

SMOKE_KIND = "smoke"
Identity = dict[str, Any]


class AdmissionError(Exception):
    """No clean matching smoke job exists, and the override was not given."""


def experiment_kind(lock: Mapping[str, Any]) -> str | None:
    kind = ((lock.get("execution") or {}).get("design") or {}).get("kind")
    return str(kind) if kind else None


def _agents(lock: Mapping[str, Any]) -> list[list[str | None]] | None:
    design = (lock.get("execution") or {}).get("design") or {}
    by_id = {entry.get("id"): entry for entry in design.get("agents") or []}
    triples = set()
    for arm in design.get("arms") or []:
        agent = by_id.get(arm.get("agent"))
        if agent is None:
            return None
        triples.add((agent.get("name"), agent.get("model"), agent.get("reasoning_effort")))
    return [list(t) for t in sorted(triples, key=repr)] or None


def identity(
    lock: Mapping[str, Any],
    runner: Mapping[str, Any] | None,
    recipes: Mapping[str, Any] | None,
    target: Mapping[str, Any] | None,
) -> Identity | None:
    """What a smoke must share with the job it clears; ``None`` when any part is unrecorded."""
    agents = _agents(lock)
    parts = (runner or {}).get("toolchain_sha256"), (recipes or {}).get("pack"), agents, target
    if not all(parts):
        return None
    assert target is not None  # ``parts`` is all-truthy
    return {
        "toolchain_sha256": parts[0],
        "pack": parts[1],
        "agents": agents,
        "target": {"name": target.get("name"), "docker": target.get("docker")},
    }


def mismatches(wanted: Identity, smoke: Identity) -> list[str]:
    found = [
        f"{key} differs"
        for key in ("toolchain_sha256", "pack", "target")
        if wanted[key] != smoke[key]
    ]
    uncovered = [a for a in wanted["agents"] if a not in smoke["agents"]]
    if uncovered:
        found.append(f"agent(s) {uncovered} not smoked")
    return found


def _read(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _trial_problem(job_dir: Path, trial_id: str) -> str | None:
    """Why one trial is not clean, or ``None``."""
    record_file = job_dir / TRIAL_RECORDS_DIR / f"{trial_id}.json"
    if not record_file.is_file():
        return f"trial {trial_id} has no record"
    try:
        record = TrialRecord.model_validate_json(record_file.read_text())
    except ValueError as error:
        return f"trial {trial_id} has an unreadable record ({type(error).__name__})"
    if not record.valid:
        failed = [c.name for c in record.validity.checks if not c.passed]
        return f"trial {trial_id} is invalid ({failed})"
    health = load_health(job_dir / record.trial_dir)
    if health is None:
        return f"trial {trial_id} has no health.json"
    if health.violations:
        return f"trial {trial_id} health violation(s) {[c.id for c in health.violations]}"
    return _no_work_problem(record)


EMPTY_TREE_DIGEST = hashlib.sha256().hexdigest()


def _no_work_problem(record: TrialRecord) -> str | None:
    """The first executed step whose agent left ``/app`` as it found it, or ``None``."""
    for result in record.step_results:
        digest = result.snapshot.digest if result.snapshot else None
        if digest is None:
            return f"trial {record.trial.trial_id} step {result.step} recorded no /app snapshot"
        if digest == (result.parent_snapshot_digest or EMPTY_TREE_DIGEST):
            return (
                f"trial {record.trial.trial_id} step {result.step}: the agent changed nothing "
                "in /app"
            )
    return None


def smoke_problem(job_dir: Path, manifest: Mapping[str, Any]) -> str | None:
    """Why a smoke job is not clean, or ``None`` when it is."""
    if "finished_at" not in manifest or "aborted" in manifest:
        return "did not finish"
    selected = manifest.get("selected") or []
    if not selected:
        return "ran no trial"
    for trial_id in selected:
        problem = _trial_problem(job_dir, str(trial_id))
        if problem:
            return problem
    return None


def _smoke_jobs(jobs_root: Path) -> list[tuple[Path, dict[str, Any], dict[str, Any]]]:
    found = []
    for manifest_file in sorted(jobs_root.glob(f"*/*/{JOB_MANIFEST}")):
        manifest = _read(manifest_file)
        lock = _read(manifest_file.parent / JOB_LOCK_FILE)
        if manifest is not None and lock is not None and experiment_kind(lock) == SMOKE_KIND:
            found.append((manifest_file.parent, manifest, lock))
    return found


def _candidate_reason(
    job_dir: Path, manifest: dict[str, Any], lock: dict[str, Any], wanted: Identity
) -> str | None:
    smoke = identity(
        lock, manifest.get("runner"), manifest.get("recipes"), manifest.get("execution_target")
    )
    if smoke is None:
        return "predates run provenance"
    differs = mismatches(wanted, smoke)
    return "; ".join(differs) if differs else smoke_problem(job_dir, manifest)


def _search(wanted: Identity | None, jobs_root: Path) -> tuple[str | None, dict[str, str]]:
    """(the clean matching smoke job, else why each smoke job did not clear the run)."""
    if wanted is None:
        return None, {"": "run provenance is incomplete"}
    reasons: dict[str, str] = {}
    for job_dir, manifest, lock in _smoke_jobs(jobs_root):
        name = str(job_dir.relative_to(jobs_root))
        reason = _candidate_reason(job_dir, manifest, lock, wanted)
        if reason is None:
            return name, reasons
        reasons[name] = reason
    return None, reasons or {"": "no smoke job found"}


def admit(
    kind: str | None,
    wanted: Identity | None,
    jobs_root: Path,
    *,
    allow_unsmoked: bool,
) -> dict[str, Any]:
    """The ``job.json`` ``admission`` record, or ``AdmissionError`` when the job is refused."""
    if kind == SMOKE_KIND:
        return {"required": False, "kind": kind}
    smoke_job, unmet = _search(wanted, jobs_root)
    if smoke_job is not None:
        return {"required": True, "kind": kind, "smoke_job": smoke_job, "overridden": False}
    if allow_unsmoked:
        return {
            "required": True,
            "kind": kind,
            "smoke_job": None,
            "overridden": True,
            "unmet": unmet,
        }
    shown = "; ".join(f"{job or 'jobs root'}: {why}" for job, why in list(unmet.items())[-5:])
    raise AdmissionError(
        f"no clean smoke job matches this {kind} experiment ({shown}). Run "
        "`make orchestrator-smoke` (or `make smoke-agents`) on the same target and code first, "
        "or ALLOW_UNSMOKED=1 to override (recorded in job.json)."
    )
