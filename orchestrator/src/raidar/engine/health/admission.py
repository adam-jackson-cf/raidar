"""Smoke-before-measure admission (mechanics-v1 design r2 §3.2; fxl ``cohort_smoke``).

``raidar run`` of any experiment kind except ``smoke`` is refused unless the jobs root holds
clean smoke jobs covering the setup: matching Raidar execution/toolchain digests, pack recipe,
the same execution target (name and Docker facts) and every arm's full identity (design 5.4,
the lock's ``execution.arms``: agent release and CLI pin, the agent's capability declaration,
model and reasoning, workflow release and resolved content hash, and the stage code digest of
each enabled stage type).
*Clean* means the job finished, every selected trial has a valid record, every trial's
``health.json`` exists with no violation, and in every trial every step's agent changed
``/app`` (the step's recorded snapshot digest differs from its parent's; the first step's parent
is the seeded workspace, or the empty tree when nothing was seeded), so a smoke where the agent
only asked a question cannot clear a run.
``--allow-unsmoked`` (``ALLOW_UNSMOKED=1``) admits without one; ``job.json`` records the
decision under ``admission``. A smoke whose stage code differs never matches, so only that
explicit, recorded override admits a job whose stage code no clean smoke ran. Arms that declare
different capabilities, or incomplete or no capabilities, are refused outright
(``capability_problem``; no override), except that an experiment declaring ``varies: [harness]``
may compare differing complete manifests; the arms then share canonical model id and reasoning
unless ``model`` / ``reasoning`` are declared too. Smoke kinds are always admitted.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar_contract.agent import Capabilities

from raidar.contract.job_layout import JOB_LOCK_FILE, JOB_MANIFEST, TRIAL_RECORDS_DIR
from raidar.contract.trial import TrialRecord
from raidar.engine.health.model import HealthReport, load_health

SMOKE_KIND = "smoke"
Identity = dict[str, Any]


class AdmissionError(Exception):
    """No clean matching smoke job exists, and the override was not given."""


def experiment_kind(lock: Mapping[str, Any]) -> str | None:
    kind = ((lock.get("execution") or {}).get("design") or {}).get("kind")
    return str(kind) if kind else None


ARM_IDENTITY_EXCLUDED = ("agent", "digest")
"""Lock arm-identity keys that name the experiment's own agent id rather than what ran."""


def _arms(lock: Mapping[str, Any], arm_ids: set[str] | None = None) -> list[dict[str, Any]] | None:
    """The selected arms' identities (sorted, deduplicated), or ``None`` when unrecorded."""
    recorded = (lock.get("execution") or {}).get("arms") or {}
    selected = set(recorded) if arm_ids is None else arm_ids
    if not selected or not selected <= set(recorded):
        return None
    identities = {
        json.dumps(
            {k: v for k, v in recorded[arm].items() if k not in ARM_IDENTITY_EXCLUDED},
            sort_keys=True,
        )
        for arm in selected
    }
    return [json.loads(item) for item in sorted(identities)]


def identity(
    lock: Mapping[str, Any],
    runner: Mapping[str, Any] | None,
    recipes: Mapping[str, Any] | None,
    target: Mapping[str, Any] | None,
    *,
    arm_ids: set[str] | None = None,
) -> Identity | None:
    """What a smoke must share with the job it clears; ``None`` when any part is unrecorded."""
    arms = _arms(lock, arm_ids)
    parts = (runner or {}).get("toolchain_sha256"), (recipes or {}).get("pack"), arms, target
    execution = (runner or {}).get("execution_sha256")
    if not all(parts) or not execution:
        return None
    assert target is not None  # ``parts`` is all-truthy
    return {
        "toolchain_sha256": parts[0],
        "execution_sha256": execution,
        "pack": parts[1],
        "arms": arms,
        "varies": list(((lock.get("execution") or {}).get("design") or {}).get("varies") or ()),
        "target": {"name": target.get("name"), "docker": target.get("docker")},
    }


def mismatches(wanted: Identity, smoke: Identity) -> list[str]:
    found = [
        f"{key} differs"
        for key in ("toolchain_sha256", "execution_sha256", "pack", "target")
        if wanted[key] != smoke[key]
    ]
    uncovered = [arm for arm in wanted["arms"] if arm not in smoke["arms"]]
    if uncovered:
        found.append(f"arm identit(ies) {uncovered} not smoked")
    return found


def _read(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


@dataclass(frozen=True)
class SmokeJob:
    """A finished smoke job: its manifest and lock, and how to read what its trials left.

    ``record`` returns a selected trial's parsed record, or why it cannot be read.
    """

    name: str
    manifest: Mapping[str, Any]
    lock: Mapping[str, Any]
    record: Callable[[str], TrialRecord | str]
    health: Callable[[TrialRecord], HealthReport | None]


def _stored_job(
    job_dir: Path, name: str, manifest: Mapping[str, Any], lock: Mapping[str, Any]
) -> SmokeJob:
    def record(trial_id: str) -> TrialRecord | str:
        record_file = job_dir / TRIAL_RECORDS_DIR / f"{trial_id}.json"
        if not record_file.is_file():
            return f"trial {trial_id} has no record"
        try:
            return TrialRecord.model_validate_json(record_file.read_text())
        except ValueError as error:
            return f"trial {trial_id} has an unreadable record ({type(error).__name__})"

    return SmokeJob(name, manifest, lock, record, lambda r: load_health(job_dir / r.trial_dir))


def _trial_problem(
    record: TrialRecord, read_health: Callable[[TrialRecord], HealthReport | None]
) -> str | None:
    """Why one trial is not clean, or ``None``."""
    trial_id = record.trial.trial_id
    if not record.valid:
        failed = [c.name for c in record.validity.checks if not c.passed]
        return f"trial {trial_id} is invalid ({failed})"
    health = read_health(record)
    if health is None:
        return f"trial {trial_id} has no health.json"
    if health.violations:
        return f"trial {trial_id} health violation(s) {[c.id for c in health.violations]}"
    return _no_work_problem(record)


EMPTY_TREE_DIGEST = hashlib.sha256().hexdigest()


def _no_work_problem(record: TrialRecord) -> str | None:
    """The first executed step whose agent left ``/app`` as it found it, or ``None``."""
    if record.agent_override:
        return f"trial {record.trial.trial_id} ran agent override {record.agent_override!r}"
    if not record.step_results:
        return f"trial {record.trial.trial_id} executed no step"
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


def job_problem(job: SmokeJob) -> tuple[list[TrialRecord], str | None]:
    """A smoke job's selected trial records, and why the job is not clean (``None``: it is)."""
    if "finished_at" not in job.manifest or "aborted" in job.manifest:
        return [], "did not finish"
    selected = job.manifest.get("selected") or []
    if not selected:
        return [], "ran no trial"
    records: list[TrialRecord] = []
    for trial_id in selected:
        record = job.record(str(trial_id))
        if isinstance(record, str):
            return [], record
        if problem := _trial_problem(record, job.health):
            return [], problem
        records.append(record)
    return records, None


def smoke_problem(job_dir: Path, manifest: Mapping[str, Any]) -> str | None:
    """Why the smoke job stored at ``job_dir`` is not clean, or ``None`` when it is."""
    return job_problem(_stored_job(job_dir, job_dir.name, manifest, {}))[1]


def _smoke_jobs(jobs_root: Path) -> list[SmokeJob]:
    found = []
    for manifest_file in sorted(jobs_root.glob(f"*/*/{JOB_MANIFEST}")):
        job_dir = manifest_file.parent
        manifest = _read(manifest_file)
        lock = _read(job_dir / JOB_LOCK_FILE)
        if manifest is not None and lock is not None and experiment_kind(lock) == SMOKE_KIND:
            found.append(_stored_job(job_dir, str(job_dir.relative_to(jobs_root)), manifest, lock))
    return found


def _candidate_identity(job: SmokeJob, wanted: Identity) -> tuple[Identity | None, str | None]:
    if job.lock.get("draft"):
        return None, "a draft lock marks a dev job: it never admits a measured run"
    records, problem = job_problem(job)
    if problem:
        return None, problem
    smoke = identity(
        job.lock,
        job.manifest.get("runner"),
        job.manifest.get("recipes"),
        job.manifest.get("execution_target"),
        arm_ids={record.trial.arm for record in records},
    )
    if smoke is None:
        return None, "run provenance or executed-arm identity is incomplete"
    differs = mismatches({**wanted, "arms": []}, smoke)
    return (None, "; ".join(differs)) if differs else (smoke, None)


def _search(wanted: Identity | None, jobs: Iterable[SmokeJob]) -> tuple[list[str], dict[str, str]]:
    """Clean compatible smoke jobs collectively covering the requested agents."""
    if wanted is None:
        return [], {"": "run provenance is incomplete"}
    reasons: dict[str, str] = {}
    remaining = list(wanted["arms"])
    selected: list[str] = []
    for job in jobs:
        smoke, reason = _candidate_identity(job, wanted)
        if smoke is None:
            reasons[job.name] = reason or "no compatible smoke identity"
            continue
        uncovered = _remaining_arms(remaining, smoke)
        if uncovered != remaining:
            selected.append(job.name)
            remaining = uncovered
        if not remaining:
            return selected, reasons
        reasons[job.name] = f"arm identit(ies) {remaining} not smoked"
    return [], reasons or {"": "no smoke job found"}


def _remaining_arms(arms: list[dict[str, Any]], smoke: Identity) -> list[dict[str, Any]]:
    return [arm for arm in arms if arm not in smoke["arms"]]


CAPABILITY_NAMES = frozenset(Capabilities.model_fields)


def _declaration_problem(declared: Sequence[Mapping[str, Any] | None]) -> str | None:
    """Why a capability declaration is absent or incomplete, or ``None``."""
    if not all(declared):
        return "an arm declares no capabilities (a lock from before declarations): relock"
    for item in declared:
        if missing := sorted(CAPABILITY_NAMES - set(item or ())):
            return f"an arm's capability declaration lacks {', '.join(missing)}"
    return None


def _confounding_problem(arms: Sequence[Mapping[str, Any]], varies: Sequence[str]) -> str | None:
    """A harness comparison's model or reasoning difference that is not declared varied."""
    for factor, label in (("model", "canonical model"), ("reasoning", "reasoning")):
        values = {arm.get(factor) for arm in arms}
        if len(values) > 1 and factor not in varies:
            return (
                f"a harness comparison differs in {label} ({sorted(map(str, values))}): "
                f"declare `{factor}` in `varies` or share it"
            )
    return None


def capability_problem(arms: Sequence[Mapping[str, Any]], varies: Sequence[str] = ()) -> str | None:
    """Why the arms cannot be compared, or ``None`` (FXL's comparing-harnesses rule).

    Each arm is ``{"capabilities", "model", "reasoning"}`` (the lock identity's keys; ``model``
    is the canonical id). Every arm must declare all nine capabilities, whatever else holds.
    Differing declarations are comparable only when the experiment declares ``varies: [harness]``;
    a harness comparison must then share the canonical model id and reasoning unless it declares
    ``model`` / ``reasoning`` varied too.
    """
    declared = [arm.get("capabilities") for arm in arms]
    if problem := _declaration_problem(declared):
        return problem
    if len({json.dumps(item, sort_keys=True) for item in declared}) > 1 and "harness" not in varies:
        return (
            "the arms declare different capabilities: declare `varies: [harness]` "
            "to compare harnesses"
        )
    return _confounding_problem(arms, varies) if "harness" in varies else None


def admit(
    kind: str | None,
    wanted: Identity | None,
    jobs_root: Path,
    *,
    allow_unsmoked: bool,
) -> dict[str, Any]:
    """The ``job.json`` ``admission`` record, or ``AdmissionError`` when the job is refused."""
    return admit_jobs(kind, wanted, lambda: _smoke_jobs(jobs_root), allow_unsmoked=allow_unsmoked)


def admit_jobs(
    kind: str | None,
    wanted: Identity | None,
    jobs: Callable[[], Iterable[SmokeJob]],
    *,
    allow_unsmoked: bool,
) -> dict[str, Any]:
    """``admit`` over the smoke jobs ``jobs`` finds (read only when the kind needs a smoke)."""
    if kind == SMOKE_KIND:
        return {"required": False, "kind": kind}
    if wanted is not None and (
        problem := capability_problem(wanted["arms"], wanted.get("varies", ()))
    ):
        raise AdmissionError(f"this {kind} experiment's arms cannot be compared: {problem}")
    smoke_jobs, unmet = _search(wanted, jobs())
    if smoke_jobs:
        return {
            "required": True,
            "kind": kind,
            "smoke_job": smoke_jobs[0],
            "smoke_jobs": smoke_jobs,
            "overridden": False,
        }
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
