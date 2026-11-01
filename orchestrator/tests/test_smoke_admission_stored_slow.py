"""Smoke-before-measure admission over smoke jobs stored on disk: discovery and record reading.

The decisions themselves are proved in memory by ``test_smoke_admission.py``; these tests keep the
real jobs-root discovery, manifest/lock parsing and trial-record reading seams.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from raidar.engine.health.admission import AdmissionError, admit
from raidar.engine.health.model import HealthReport, write_health
from tests.test_smoke_admission import (
    RECIPES,
    RUNNER,
    TARGET,
    TRIAL_ID,
    lock,
    trial_record,
    wanted,
)

pytestmark = pytest.mark.slow


def stored_job(root: Path, name: str) -> Path:
    """A finished, clean smoke job stored under ``root/exp/<name>``."""
    job_dir = root / "exp" / name
    record_file = job_dir / "trial-records" / f"{TRIAL_ID}.json"
    record_file.parent.mkdir(parents=True)
    record_file.write_text(trial_record("arm-luna").model_dump_json())
    manifest = {
        "runner": RUNNER,
        "recipes": RECIPES,
        "execution_target": TARGET,
        "selected": [TRIAL_ID],
        "finished_at": "2026-01-01T00:00:00+00:00",
    }
    (job_dir / "job.json").write_text(json.dumps(manifest))
    (job_dir / "job.lock.json").write_text(json.dumps(lock("smoke")))
    write_health(job_dir / "trials" / TRIAL_ID, HealthReport(trial_id=TRIAL_ID, checks=()))
    return job_dir


def decide(root: Path, **kwargs: Any) -> dict[str, Any]:
    return admit("ab-test", wanted(), root, **{"allow_unsmoked": False, **kwargs})


def test_a_stored_clean_smoke_admits_and_is_recorded(tmp_path: Path) -> None:
    stored_job(tmp_path, "smoke-01")
    assert decide(tmp_path) == {
        "required": True,
        "kind": "ab-test",
        "smoke_job": "exp/smoke-01",
        "smoke_jobs": ["exp/smoke-01"],
        "overridden": False,
    }


def test_a_smoke_with_a_missing_or_unreadable_trial_record_is_not_clean(tmp_path: Path) -> None:
    missing = stored_job(tmp_path, "missing")
    (missing / "trial-records" / f"{TRIAL_ID}.json").unlink()
    garbled = stored_job(tmp_path, "garbled")
    (garbled / "trial-records" / f"{TRIAL_ID}.json").write_text("{not json")

    with pytest.raises(AdmissionError) as raised:
        decide(tmp_path)

    assert f"missing: trial {TRIAL_ID} has no record" in str(raised.value)
    assert f"garbled: trial {TRIAL_ID} has an unreadable record" in str(raised.value)


def test_a_stored_smoke_without_a_health_file_is_not_clean(tmp_path: Path) -> None:
    stored_job(tmp_path, "no-health")
    (tmp_path / "exp" / "no-health" / "trials" / TRIAL_ID / "health.json").unlink()

    with pytest.raises(AdmissionError, match="no health.json"):
        decide(tmp_path)


def test_a_job_with_an_unreadable_manifest_or_lock_is_no_smoke_candidate(tmp_path: Path) -> None:
    broken = stored_job(tmp_path, "broken-manifest")
    (broken / "job.json").write_text("[1, 2]")
    unlocked = stored_job(tmp_path, "broken-lock")
    (unlocked / "job.lock.json").write_text("{not json")

    recorded = decide(tmp_path, allow_unsmoked=True)

    assert recorded["unmet"] == {"": "no smoke job found"}
