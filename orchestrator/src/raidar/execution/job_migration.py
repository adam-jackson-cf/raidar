"""Move a legacy job onto the schema v2 layout and lock (``raidar migrate-job``).

A job written before schema v2 (``run.json``, ``run.lock.json``,
``trajectories/``) executed under a lock of version 2 (or 1) whose spec used
the v1 vocabulary. The same experiment, upgraded to schema v2 and relocked,
has a new ``lock_sha256``: the design is spelled differently (``kind``, an id
in the ``<runtime>__<subject>__<intent>`` grammar, ``runtime``/``environment``,
agent ``name``, ``attempts``) and so are the digests derived from it. The job
moves to the new lock only when that is provably the same experiment:

1. the executed lock is a valid sealed lock and the one ``run.json`` records;
2. the current lock is a valid sealed lock of this version;
3. the executed lock's execution section, translated field by field into the
   v2 vocabulary (``translate_execution``), equals the current lock's
   execution section. Content digests (problems, files, component and problem
   references, hashed conditions, tools) must be byte-equal; the arm digests
   are recomputed from the translated agents after the executed ones are
   checked against their own inputs. The new id and kind are the only facts
   the translation takes from the current lock, and the record names both.

On success the legacy files move, byte for byte, into ``legacy-v1/``
(``run.json``, ``run.lock.json``, ``plan.json``, ``trajectories/``), and the job
gets ``job.json`` (with a ``job_migration`` provenance record holding the
sha256 of every legacy file), ``job.lock.json`` (the current lock),
``plan.json`` and ``trial-records/`` in the v2 vocabulary. Harbor trial
directories, tasks and the assessment config are never touched; the trial
hashes keep the digest the trials executed under. A job under a directory
named after its old experiment id moves to one named after the new id.
Re-running the migration on a migrated job is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.agents.config import agent_name_for
from raidar.execution.models import TrialRecord
from raidar.job_layout import (
    ARCHIVED_LEGACY_DIR,
    JOB_KIND,
    JOB_LOCK_FILE,
    JOB_MANIFEST,
    LEGACY_RUN_LOCK_FILE,
    LEGACY_RUN_MANIFEST,
    LEGACY_TRAJECTORIES_DIR,
    PLAN_FILE,
    TRIAL_RECORDS_DIR,
)
from raidar.spec.canonical import digest_json
from raidar.spec.lock import EXECUTION_REF_KINDS, LOCK_VERSION, lock_integrity_issues
from raidar.spec.schema_v1 import FACTOR_RENAMES, upgrade_agent

LEGACY_DIR = ARCHIVED_LEGACY_DIR
LEGACY_FILES = (LEGACY_RUN_MANIFEST, LEGACY_RUN_LOCK_FILE, PLAN_FILE)
MIGRATED_LOCK_VERSIONS = (1, 2)
LEGACY_DESIGN_FIELDS = (
    "id",
    "problems",
    "runtime",
    "agents",
    "protocol",
    "factors",
    "shared_components",
    "conditions",
    "arms",
)
REASON_RENAMES = (
    ("no_oracle_record", "no_verifier_record"),
    ("oracle_infra", "verifier_infra"),
    ("oracle_leak", "verifier_leak"),
)
CHECK_RENAMES = {
    "oracle_verdicts": "verifier_verdicts",
    "oracle_infra": "verifier_infra",
    "no_oracle_leak": "no_verifier_leak",
    "harness_version": "agent_version",
}
PHASE_RENAMES = {"oracle_leak_check": "verifier_leak_check", "oracle_run": "verifier_run"}


class JobMigrationError(ValueError):
    """The job cannot be moved onto the current lock."""


@dataclass(frozen=True)
class JobMigration:
    job_dir: Path
    executed_lock_sha256: str
    lock_sha256: str
    migrated: bool
    """False when the job already carried this migration (no-op)."""


def _read_object(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise JobMigrationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise JobMigrationError(f"{path} is not a JSON object")
    return loaded


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


# ---- the executed lock -------------------------------------------------------


def _legacy_execution_section(spec_json: Mapping[str, Any], **parts: object) -> dict[str, object]:
    """A version-2 lock's execution section, built the way lock version 2 built it."""
    analysis = spec_json["analysis"]
    return {
        "design": {
            **{name: spec_json[name] for name in LEGACY_DESIGN_FIELDS if name in spec_json},
            **{name: analysis[name] for name in ("blocks", "seed")},
        },
        **parts,
    }


def _execution_from_version_one(
    executed: Mapping[str, Any], current_tools: Mapping[str, str]
) -> dict[str, object]:
    """The version-2 execution section a version-1 lock's flat records describe.

    Version 1 recorded tool versions without their reference kind, so each tool
    the current execution section pins is taken from that record.
    """
    versions = executed["versions"]
    recorded = versions.get("tools") or {}
    references = executed.get("references") or {}
    return _legacy_execution_section(
        executed["spec"],
        problems=executed["problems"],
        files=executed["files"],
        references={kind.value: references.get(kind.value, {}) for kind in EXECUTION_REF_KINDS},
        conditions=executed["conditions"],
        arms=executed["arms"],
        versions={"harbor": versions["harbor"], "harnesses": versions["harnesses"]},
        tools={tool: recorded[tool] for tool in current_tools if tool in recorded},
    )


def executed_execution(
    executed: Mapping[str, Any], path: Path, current_tools: Mapping[str, str]
) -> tuple[str, dict[str, Any]]:
    """(lock_sha256, version-2-shaped execution section) of a verified executed lock."""
    version = executed.get("lock_version")
    if version == 1:
        body = {key: value for key, value in executed.items() if key != "lock_sha256"}
        if digest_json(body) != executed.get("lock_sha256"):
            raise JobMigrationError(f"{path}: lock_sha256 does not match the lock body")
        return str(executed["lock_sha256"]), _execution_from_version_one(executed, current_tools)
    if version == 2:
        issues = lock_integrity_issues(executed, version=2)
        if issues:
            raise JobMigrationError(f"{path} is not a valid version-2 lock: {'; '.join(issues)}")
        return str(executed["lock_sha256"]), dict(executed["execution"])
    raise JobMigrationError(
        f"{path} is lock_version {version!r}; only versions {MIGRATED_LOCK_VERSIONS} are migrated"
    )


# ---- vocabulary translation --------------------------------------------------


def _translated_design(design: Mapping[str, Any], *, kind: str, experiment_id: str) -> dict:
    runtime = design["runtime"]
    translated: dict[str, Any] = {
        "kind": kind,
        "id": experiment_id,
        "problems": design["problems"],
        "runtime": runtime["language"],
        "environment": {"type": runtime["environment"], "compose_init": runtime["compose_init"]},
        "agents": [upgrade_agent(dict(agent)) for agent in design["agents"]],
        "protocol": design["protocol"],
        "factors": [FACTOR_RENAMES.get(factor, factor) for factor in design["factors"]],
        "conditions": design["conditions"],
        "arms": design["arms"],
        "attempts": design["blocks"],
        "seed": design["seed"],
    }
    if "shared_components" in design:
        translated["shared_components"] = design["shared_components"]
    return translated


def _component_references(references: Mapping[str, Any]) -> dict[str, Any]:
    translated = {}
    for ref, record in references.items():
        metadata = dict(record.get("metadata") or {})
        if "supports" in metadata:
            metadata["supports"] = [agent_name_for(cli).value for cli in metadata["supports"]]
        translated[ref] = {**record, "metadata": metadata}
    return translated


def _agent_body(agent: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in agent.items() if key != "id"}


def _translated_arms(execution: Mapping[str, Any]) -> dict[str, Any]:
    """Arm digests recomputed from translated agents, after checking the executed ones."""
    agents = {agent["id"]: agent for agent in execution["design"]["agents"]}
    conditions = execution["conditions"]
    arms = {}
    for arm_id, record in execution["arms"].items():
        agent, condition = agents[record["agent"]], conditions[record["condition"]]
        executed = digest_json({"agent": _agent_body(agent), "condition": condition})
        if executed != record["digest"]:
            raise JobMigrationError(
                f"the executed lock's arm {arm_id!r} digest does not match its own agent and "
                "condition; the lock cannot be translated"
            )
        translated = _agent_body(upgrade_agent(dict(agent)))
        arms[arm_id] = {
            **record,
            "digest": digest_json({"agent": translated, "condition": condition}),
        }
    return arms


def translate_execution(
    execution: Mapping[str, Any], *, kind: str, experiment_id: str
) -> dict[str, Any]:
    """The schema v2 execution section of a version-2-shaped (v1 vocabulary) one."""
    references = execution["references"]
    harnesses = execution["versions"]["harnesses"]
    return {
        "design": _translated_design(execution["design"], kind=kind, experiment_id=experiment_id),
        "problems": execution["problems"],
        "files": execution["files"],
        "references": {
            "problem": references.get("problem", {}),
            "component": _component_references(references.get("component", {})),
        },
        "conditions": execution["conditions"],
        "arms": _translated_arms(execution),
        "versions": {
            "harbor": execution["versions"]["harbor"],
            "agents": {
                agent_id: {
                    "name": agent_name_for(entry["harness"]).value,
                    "version": entry["version"],
                }
                for agent_id, entry in harnesses.items()
            },
        },
        "tools": execution["tools"],
    }


def differences(expected: Any, found: Any, path: str = "execution") -> list[str]:
    """Dotted paths where two JSON values differ (leaves only)."""
    if isinstance(expected, Mapping) and isinstance(found, Mapping):
        return [
            item
            for key in sorted(set(expected) | set(found))
            for item in differences(expected.get(key), found.get(key), f"{path}.{key}")
        ]
    return [] if expected == found else [path]


# ---- job files ---------------------------------------------------------------


def _renamed_reason(reason: str) -> str:
    for old, new in REASON_RENAMES:
        if reason == old or reason.startswith(f"{old}:"):
            return new + reason[len(old) :]
    return reason


def _renamed_detail(detail: str) -> str:
    for old, new in REASON_RENAMES:
        detail = detail.replace(old, new)
    return detail


def translate_planned_trial(trial: Mapping[str, Any]) -> dict[str, Any]:
    translated = {key: value for key, value in trial.items() if key != "block"}
    translated["attempt"] = trial["block"]
    return translated


def _translate_step(step: Mapping[str, Any]) -> dict[str, Any]:
    renamed = {"block", "oracle_record", "kind", "schema_version", "infra_reasons"}
    translated = {key: value for key, value in step.items() if key not in renamed}
    translated["kind"] = "raidar-step-result"
    translated["schema_version"] = 2
    translated["attempt"] = step["block"]
    translated["verifier_record"] = step.get("oracle_record")
    translated["infra_reasons"] = [_renamed_reason(r) for r in step.get("infra_reasons") or ()]
    return translated


def translate_trial_record(record: Mapping[str, Any], *, experiment_id: str) -> dict[str, Any]:
    """A ``raidar-trajectory`` v1 record in the ``raidar-trial`` v2 vocabulary (validated)."""
    if record.get("kind") != "raidar-trajectory":
        raise JobMigrationError(f"not a legacy trajectory record (kind {record.get('kind')!r})")
    renamed = {"kind", "schema_version", "experiment", "run_id", "trial", "attempts"}
    translated = {key: value for key, value in record.items() if key not in renamed}
    translated["kind"] = "raidar-trial"
    translated["schema_version"] = 2
    translated["experiment"] = experiment_id
    translated["job_id"] = record["run_id"]
    translated["trial"] = translate_planned_trial(record["trial"])
    translated["step_results"] = [_translate_step(step) for step in record["attempts"]]
    translated["validity"] = {
        "checks": [
            {
                **check,
                "name": CHECK_RENAMES.get(check["name"], check["name"]),
                "detail": _renamed_detail(str(check.get("detail", ""))),
            }
            for check in record["validity"]["checks"]
        ]
    }
    translated["phases"] = [
        {**phase, "phase": PHASE_RENAMES.get(phase["phase"], phase["phase"])}
        for phase in record.get("phases") or ()
    ]
    return TrialRecord.model_validate(translated).model_dump(mode="json")


def translate_plan(
    plan: Mapping[str, Any], *, experiment_id: str, lock_sha256: str
) -> dict[str, Any]:
    options = dict(plan.get("options") or {})
    options["attempts"] = options.pop("blocks", [])
    return {
        **{key: value for key, value in plan.items() if key not in {"options", "trials"}},
        "schema_version": 2,
        "experiment": experiment_id,
        "lock_sha256": lock_sha256,
        "executed_lock_sha256": plan.get("lock_sha256"),
        "options": options,
        "trials": [translate_planned_trial(trial) for trial in plan.get("trials", ())],
    }


def translate_manifest(
    manifest: Mapping[str, Any],
    *,
    experiment_id: str,
    lock_sha256: str,
    experiment_file: Path,
) -> dict[str, Any]:
    dropped = {"kind", "run_id", "experiment", "lock_sha256", "experiment_file", "summary"}
    translated = {key: value for key, value in manifest.items() if key not in dropped}
    summary = dict(manifest.get("summary") or {})
    if "trajectories" in summary:
        summary["trials"] = summary.pop("trajectories")
    return {
        **translated,
        "kind": JOB_KIND,
        "experiment": experiment_id,
        "job_id": manifest.get("run_id"),
        "lock_sha256": lock_sha256,
        "experiment_file": str(experiment_file),
        "summary": summary,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _legacy_inventory(job_dir: Path) -> list[Path]:
    files = [job_dir / name for name in LEGACY_FILES if (job_dir / name).is_file()]
    files += sorted((job_dir / LEGACY_TRAJECTORIES_DIR).glob("*.json"))
    return files


# ---- the migration -------------------------------------------------------------


def _current_lock(lock_path: Path) -> dict[str, Any]:
    current = _read_object(lock_path)
    issues = lock_integrity_issues(current)
    if issues:
        raise JobMigrationError(f"{lock_path} is not a valid lock: {'; '.join(issues)}")
    return current


def _already_migrated(job_dir: Path, lock_sha256: str) -> JobMigration | None:
    manifest_path = job_dir / JOB_MANIFEST
    if not manifest_path.is_file():
        return None
    if (job_dir / LEGACY_RUN_MANIFEST).is_file():
        raise JobMigrationError(f"{job_dir} has both {JOB_MANIFEST} and {LEGACY_RUN_MANIFEST}")
    manifest = _read_object(manifest_path)
    record = manifest.get("job_migration")
    if not isinstance(record, Mapping) or manifest.get("lock_sha256") != lock_sha256:
        raise JobMigrationError(
            f"{job_dir} is already a v2 job (lock {str(manifest.get('lock_sha256'))[:12]}); "
            "nothing to migrate onto this lock"
        )
    return JobMigration(job_dir, str(record["from"]["lock_sha256"]), lock_sha256, migrated=False)


def _verified(
    job_dir: Path, executed_lock: Path, current: Mapping[str, Any], lock_path: Path
) -> tuple[dict[str, Any], str]:
    manifest = _read_object(job_dir / LEGACY_RUN_MANIFEST)
    executed_sha, execution = executed_execution(
        _read_object(executed_lock), executed_lock, current["execution"].get("tools") or {}
    )
    if manifest.get("lock_sha256") != executed_sha:
        raise JobMigrationError(
            f"{job_dir} was executed under lock {manifest.get('lock_sha256')}, "
            f"not {executed_sha} ({executed_lock})"
        )
    design = current["execution"]["design"]
    translated = translate_execution(execution, kind=design["kind"], experiment_id=design["id"])
    differing = differences(translated, current["execution"])
    if differing:
        raise JobMigrationError(
            f"{lock_path} does not describe the experiment {job_dir} executed: "
            f"{', '.join(differing[:20])} differ"
        )
    return manifest, executed_sha


def _experiment_file(current: Mapping[str, Any], lock_path: Path) -> Path:
    return (lock_path.resolve().parent / str(current["experiment"]["source"])).resolve()


def _relocated(job_dir: Path, old_id: str, new_id: str) -> Path:
    if job_dir.parent.name != old_id or old_id == new_id:
        return job_dir
    target = job_dir.parent.parent / new_id / job_dir.name
    if target.exists():
        raise JobMigrationError(f"cannot move {job_dir}: {target} exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    job_dir.rename(target)
    if not any(job_dir.parent.iterdir()):
        job_dir.parent.rmdir()
    return target


def migrate_job(
    job_dir: Path,
    lock_path: Path,
    *,
    executed_lock: Path | None = None,
    relocate: bool = True,
) -> JobMigration:
    """Move a legacy job onto ``lock_path`` after proving the execution is identical."""
    job_dir = job_dir.resolve()
    current = _current_lock(lock_path)
    target_sha = str(current["lock_sha256"])
    done = _already_migrated(job_dir, target_sha)
    if done is not None:
        return done
    executed_lock = executed_lock or job_dir / LEGACY_RUN_LOCK_FILE
    manifest, executed_sha = _verified(job_dir, executed_lock, current, lock_path)
    design = current["execution"]["design"]
    new_id, kind, old_id = str(design["id"]), str(design["kind"]), str(manifest["experiment"])
    legacy_files = _legacy_inventory(job_dir)
    hashes = {path.relative_to(job_dir).as_posix(): _sha256(path) for path in legacy_files}
    records = {
        path.stem: translate_trial_record(_read_object(path), experiment_id=new_id)
        for path in sorted((job_dir / LEGACY_TRAJECTORIES_DIR).glob("*.json"))
    }
    plan = translate_plan(
        _read_object(job_dir / PLAN_FILE), experiment_id=new_id, lock_sha256=target_sha
    )
    legacy = job_dir / LEGACY_DIR
    legacy.mkdir()
    for path in legacy_files:
        destination = legacy / path.relative_to(job_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), destination)
    (job_dir / LEGACY_TRAJECTORIES_DIR).rmdir()
    if executed_lock.resolve().parent != job_dir:
        shutil.copyfile(executed_lock, legacy / LEGACY_RUN_LOCK_FILE)
        hashes[LEGACY_RUN_LOCK_FILE] = _sha256(executed_lock)
    for trial_id, record in records.items():
        _write_json(job_dir / TRIAL_RECORDS_DIR / f"{trial_id}.json", record)
    _write_json(job_dir / PLAN_FILE, plan)
    (job_dir / JOB_LOCK_FILE).write_bytes(lock_path.read_bytes())
    job = translate_manifest(
        manifest,
        experiment_id=new_id,
        lock_sha256=target_sha,
        experiment_file=_experiment_file(current, lock_path),
    )
    job["job_migration"] = {
        "from": {
            "experiment": old_id,
            "lock_sha256": executed_sha,
            "layout": "run.json, run.lock.json, plan.json, trajectories/ (schema v1)",
        },
        "to": {"experiment": new_id, "kind": kind, "lock_sha256": target_sha},
        "lock_version": LOCK_VERSION,
        "legacy_dir": LEGACY_DIR,
        "legacy_sha256": dict(sorted(hashes.items())),
        "verified": (
            "the executed lock's execution section, translated into the schema v2 "
            "vocabulary, equals the current lock's execution section"
        ),
    }
    _write_json(job_dir / JOB_MANIFEST, job)
    moved = _relocated(job_dir, old_id, new_id) if relocate else job_dir
    return JobMigration(moved, executed_sha, target_sha, migrated=True)
