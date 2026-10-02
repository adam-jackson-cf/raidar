"""Move a schema v2 job onto a relock that only pinned problem errata (``raidar migrate-job``).

Registering or changing a problem's errata changes the lock's execution
section (``execution.references.problem.<ref>.metadata.errata``) and therefore
``lock_sha256``. A job that ran before then executed exactly the same trials:
an errata never changes what runs or the raw grade, only the errata-applied
verdict the verifier records beside it. Such a job moves to the new lock only
when that is provable:

1. the job's ``job.lock.json`` is a valid sealed lock and the one ``job.json`` records;
2. the target lock is a valid sealed lock;
3. both execution sections, with every problem's ``errata`` metadata removed,
   are equal.

On success the executed lock is kept byte for byte under
``lock-history/<lock_sha256>.lock.json``; ``job.lock.json`` becomes the target
lock; ``job.json`` and ``plan.json`` name the new ``lock_sha256``; and
``job.json`` gains a ``lock_migrations`` entry with both digests, the errata
digests before and after, and what was verified. Trial directories, trial
records (their hashes keep the digest they executed under), tasks and the
assessment config are never touched. The job's step records carry no errata
outcome; ``correctness@1`` recomputes errata-applied verdicts from their raw cases.
The raw analysis stays reproducible: every raw metric is unchanged by an errata.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from raidar.job_layout import JOB_LOCK_FILE, JOB_MANIFEST, PLAN_FILE
from raidar.spec.lock import lock_integrity_issues

LOCK_HISTORY_DIR = "lock-history"
MIGRATION_KIND = "errata_adoption"


class ErrataMigrationError(ValueError):
    """The job's executed lock differs from the target in more than problem errata."""


def _read(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ErrataMigrationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ErrataMigrationError(f"{path} is not a JSON object")
    return loaded


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _problem_references(execution: Mapping[str, Any]) -> dict[str, Any]:
    references = execution.get("references")
    problems = references.get("problem") if isinstance(references, Mapping) else None
    return dict(problems) if isinstance(problems, Mapping) else {}


def errata_digests(execution: Mapping[str, Any]) -> dict[str, str | None]:
    """``{problem ref: errata sha256 or None}`` of an execution section."""
    digests: dict[str, str | None] = {}
    for ref, record in _problem_references(execution).items():
        errata = (record.get("metadata") or {}).get("errata")
        digests[ref] = str(errata["sha256"]) if isinstance(errata, Mapping) else None
    return digests


def without_errata(execution: Mapping[str, Any]) -> dict[str, Any]:
    """The execution section with every problem reference's ``errata`` metadata removed."""
    stripped = copy.deepcopy(dict(execution))
    for record in _problem_references(stripped).values():
        metadata = record.get("metadata")
        if isinstance(metadata, dict):
            metadata.pop("errata", None)
    return stripped


def _differences(expected: Any, found: Any, path: str = "execution") -> list[str]:
    if isinstance(expected, Mapping) and isinstance(found, Mapping):
        return [
            item
            for key in sorted(set(expected) | set(found))
            for item in _differences(expected.get(key), found.get(key), f"{path}.{key}")
        ]
    return [] if expected == found else [path]


def _executed_lock(job_dir: Path, manifest: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    path = job_dir / JOB_LOCK_FILE
    raw = path.read_bytes() if path.is_file() else b""
    if not raw:
        raise ErrataMigrationError(f"{job_dir} has no {JOB_LOCK_FILE} to verify against")
    executed = _read(path)
    issues = lock_integrity_issues(executed)
    if issues:
        raise ErrataMigrationError(f"{path} is not a valid lock: {'; '.join(issues)}")
    if executed["lock_sha256"] != manifest.get("lock_sha256"):
        raise ErrataMigrationError(
            f"{path} ({str(executed['lock_sha256'])[:12]}) is not the lock {JOB_MANIFEST} "
            f"records ({str(manifest.get('lock_sha256'))[:12]})"
        )
    return executed, raw


def adopt_errata(job_dir: Path, target: Mapping[str, Any], target_bytes: bytes) -> tuple[str, str]:
    """Move ``job_dir`` onto ``target``; return (executed, target) ``lock_sha256``.

    Raises ``ErrataMigrationError`` unless the two locks differ only in problem errata.
    """
    manifest = _read(job_dir / JOB_MANIFEST)
    executed, raw = _executed_lock(job_dir, manifest)
    executed_sha, target_sha = str(executed["lock_sha256"]), str(target["lock_sha256"])
    differing = _differences(
        without_errata(executed["execution"]), without_errata(target["execution"])
    )
    if differing:
        raise ErrataMigrationError(
            f"the target lock differs from the lock {job_dir} executed under in more than "
            f"problem errata: {', '.join(differing[:20])}"
        )
    before, after = errata_digests(executed["execution"]), errata_digests(target["execution"])
    history = job_dir / LOCK_HISTORY_DIR
    history.mkdir(exist_ok=True)
    kept = history / f"{executed_sha}.lock.json"
    kept.write_bytes(raw)
    (job_dir / JOB_LOCK_FILE).write_bytes(target_bytes)
    plan = _read(job_dir / PLAN_FILE)
    plan.setdefault("executed_lock_sha256", executed_sha)
    plan["lock_sha256"] = target_sha
    _write(job_dir / PLAN_FILE, plan)
    entry = {
        "kind": MIGRATION_KIND,
        "from": {"lock_sha256": executed_sha},
        "to": {"lock_sha256": target_sha},
        "executed_lock": kept.relative_to(job_dir).as_posix(),
        "executed_lock_file_sha256": hashlib.sha256(raw).hexdigest(),
        "errata": {
            ref: {"from": before.get(ref), "to": after.get(ref)}
            for ref in sorted(set(before) | set(after))
            if before.get(ref) != after.get(ref)
        },
        "verified": (
            "the executed and target execution sections are equal once every problem's "
            "errata metadata is removed; raw grades are unchanged, errata-applied verdicts "
            "are recomputed from the raw cases"
        ),
    }
    manifest["lock_sha256"] = target_sha
    manifest["lock_migrations"] = [*manifest.get("lock_migrations", []), entry]
    _write(job_dir / JOB_MANIFEST, manifest)
    return executed_sha, target_sha
