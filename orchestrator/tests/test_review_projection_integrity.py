"""Both review producers apply the analysis provenance and sealed-record gate."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from raidar.engine.health.records_manifest import seal_trial
from raidar.evaluation.analysis.pooling import PoolingError
from raidar.evaluation.review.projection import merge_review_projections
from raidar.evaluation.review.quality.projection import build_quality_projection
from tests.review_fixtures.projection.generate import REVIEW_ONLY_DIR, generate_review_fixture


@pytest.fixture(scope="module")
def review_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("review-integrity")
    generate_review_fixture(root)
    return root


def _copy_job(root: Path, target: Path) -> Path:
    source = next((root / REVIEW_ONLY_DIR).glob("*/*/job.json")).parent
    shutil.copytree(source, target)
    return target


PARITY = {
    "image_id": "sha256:image",
    "seed_digest": "seed",
    "limits": {"agent_timeout_s": 1800.0},
    "network": [{"network_mode": "allowlist", "allowed_hosts": ["api.openai.com"]}],
}


def _modern(job: Path, **changes: object) -> None:
    """Give a historical fixture job what a job of the current epoch records, then seal it."""
    plan_path = job / "plan.json"
    plan = json.loads(plan_path.read_text())
    for path in (job / "trial-records").glob("*.json"):
        record = json.loads(path.read_text())
        arm = plan.setdefault("arms", {}).setdefault(record["trial"]["arm"], {})
        arm["capabilities"] = {"isolation_proof": "full"}
        path.write_text(json.dumps({**record, "parity": PARITY}))
    plan_path.write_text(json.dumps(plan))
    manifest_path = job / "job.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(
        job_id=job.name,
        mechanics_epoch="mechanics-v1",
        runner={"dirty": False, "toolchain_sha256": "toolchain", "execution_sha256": "execution"},
        recipes={"pack": "pack", "graders": "grader"},
        effective_limits={"agent_s": 1800},
        execution_target={"name": "target-a", "docker": {"arch": "arm64"}},
        records_manifests={
            record.stem: seal_trial(job / "trials" / record.stem, record, job)
            for record in (job / "trial-records").glob("*.json")
        },
    )
    manifest.update(changes)
    manifest_path.write_text(json.dumps(manifest))


def _project(pipeline: str, jobs: Path, store: Path):
    if pipeline == "quality":
        return build_quality_projection(jobs, store)
    return merge_review_projections(job_roots=[jobs], stores=[store])


@pytest.mark.parametrize("pipeline", ["main", "quality"])
@pytest.mark.parametrize(
    "change", ["target", "toolchain", "execution", "pack", "grader", "limits", "dirty", "epoch"]
)
def test_review_refuses_incompatible_provenance_before_combining_jobs(
    review_fixture: Path, tmp_path: Path, pipeline: str, change: str
) -> None:
    one = _copy_job(review_fixture, tmp_path / "one")
    two = _copy_job(review_fixture, tmp_path / "two")
    _modern(one)
    _modern(two)
    manifest_path = two / "job.json"
    manifest = json.loads(manifest_path.read_text())
    if change == "target":
        manifest["execution_target"]["name"] = "target-b"
    elif change in {"toolchain", "execution"}:
        manifest["runner"][f"{change}_sha256"] = "another"
    elif change in {"pack", "grader"}:
        manifest["recipes"]["graders" if change == "grader" else "pack"] = "another"
    elif change == "limits":
        manifest["effective_limits"]["agent_s"] = 60
    elif change == "dirty":
        manifest["runner"]["dirty"] = True
    else:
        manifest["mechanics_epoch"] = "another-epoch"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(PoolingError, match="cannot be pooled"):
        _project(pipeline, tmp_path, review_fixture / "store")


@pytest.mark.parametrize("pipeline", ["main", "quality"])
def test_review_checks_a_single_jobs_records_and_preserves_unmeasured_historical_review(
    review_fixture: Path, tmp_path: Path, pipeline: str
) -> None:
    job = _copy_job(review_fixture, tmp_path / "one")
    historical = _project(pipeline, job, review_fixture / "store")
    if pipeline == "quality":
        assert all(
            any("provenance unmeasured" in note for note in detail.notes)
            for detail in historical.run_details.values()
        )
    else:
        assert all(
            any(note.category == "provenance-unmeasured" for note in detail.annotations)
            for detail in historical.run_details.values()
        )
    _modern(job)
    assert _project(pipeline, job, review_fixture / "store").runs
    record = next((job / "trial-records").glob("*.json"))
    record.write_text(record.read_text() + "\n")
    with pytest.raises(PoolingError, match="records manifest violation.*never overridable"):
        _project(pipeline, job, review_fixture / "store")
