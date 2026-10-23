"""A trial's records manifest: sha256 of every file under its directory and of its trial record.

Written once the trial record exists (``raidar-records-manifest.json`` in the trial
directory); ``job.json`` keeps each manifest's own sha256. ``raidar analyze`` recomputes
both, so an edited, added or removed evidence file or record is a tamper finding.
Paths are relative to the job directory; the manifest does not list itself, nor the
trial's ``health.json``: the audit is derived from the sealed files and is recomputed by
``raidar health`` (whose own check ``T2`` is this manifest's verification).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from raidar.execution.health_model import HEALTH_FILE

RECORDS_MANIFEST = "raidar-records-manifest.json"


def _file_digest(path: Path) -> str:
    if path.is_symlink():
        return "symlink:" + hashlib.sha256(os.readlink(path).encode()).hexdigest()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_files(trial_dir: Path, record_file: Path, job_dir: Path) -> dict[str, str]:
    """sha256 of the trial directory's files (not its manifest) and the trial record."""
    files: dict[str, str] = {}
    for current, _, names in os.walk(trial_dir):
        for name in names:
            path = Path(current, name)
            if name in (RECORDS_MANIFEST, HEALTH_FILE) and path.parent == trial_dir:
                continue
            files[path.relative_to(job_dir).as_posix()] = _file_digest(path)
    if record_file.is_file():
        files[record_file.relative_to(job_dir).as_posix()] = _file_digest(record_file)
    return dict(sorted(files.items()))


def seal_trial(trial_dir: Path, record_file: Path, job_dir: Path) -> str:
    """Write the trial's records manifest and return its sha256 (``job.json`` keeps it)."""
    payload = (
        json.dumps(
            {"files": current_files(trial_dir, record_file, job_dir)}, indent=2, sort_keys=True
        )
        + "\n"
    ).encode()
    (trial_dir / RECORDS_MANIFEST).write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _sealed_state(manifest: Path, expected: str | None) -> tuple[dict[str, str], str | None]:
    """The sealed file hashes, or the reason the manifest cannot be trusted."""
    if expected is None:
        return {}, "no records manifest digest in job.json"
    if not manifest.is_file():
        return {}, f"{RECORDS_MANIFEST} is missing"
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        return {}, f"{RECORDS_MANIFEST} differs from the digest in job.json"
    return json.loads(raw)["files"], None


def tamper_findings(
    trial_dir: Path, record_file: Path, job_dir: Path, expected: str | None
) -> list[str]:
    """What differs from the sealed manifest (empty: intact); ``expected`` is job.json's sha256."""
    sealed, reason = _sealed_state(trial_dir / RECORDS_MANIFEST, expected)
    if reason is not None:
        return [reason]
    now = current_files(trial_dir, record_file, job_dir)
    changed = [f"{path} changed" for path in sealed if path in now and sealed[path] != now[path]]
    missing = [f"{path} missing" for path in sealed if path not in now]
    added = [f"{path} added" for path in now if path not in sealed]
    return [*changed, *missing, *added]
