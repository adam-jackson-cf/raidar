"""The pooling gate: refusal kinds, tamper, overrides, pre-epoch jobs, timing across targets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from raidar.analysis.pooling import PoolingError, check_pooling
from raidar.analysis.sources import JobDir
from raidar.execution.health.records_manifest import RECORDS_MANIFEST, seal_trial

TRIAL = "t1"


def _manifest(**changes: Any) -> dict[str, Any]:
    base = {
        "job_id": "job",
        "mechanics_epoch": "mechanics-v1",
        "runner": {"commit": "abc", "dirty": False, "toolchain_sha256": "tc1"},
        "recipes": {"pack": "pack1", "graders": {"scb/forge@x": "g1"}},
        "effective_limits": {"agent_s": {"a": 1800.0}, "verifier_s": 1500.0},
        "execution_target": {
            "name": "mbp",
            "docker": {
                "os": "linux",
                "arch": "arm64",
                "ncpu": 8,
                "mem_total": 1,
                "server_version": "1",
            },
        },
    }
    return {**base, **changes}


def _job(root: Path, name: str, manifest: dict[str, Any] | None = None, **plan: Any) -> JobDir:
    """A job directory with one sealed trial; ``manifest`` is its job.json (default: epoch job)."""
    path = root / name
    (path / "trials" / TRIAL).mkdir(parents=True)
    (path / "trial-records").mkdir()
    (path / "trials" / TRIAL / "result.json").write_text("{}")
    record = path / "trial-records" / f"{TRIAL}.json"
    record.write_text("{}")
    fields = _manifest(job_id=name) if manifest is None else {**manifest, "job_id": name}
    if "mechanics_epoch" in fields:
        fields["records_manifests"] = {TRIAL: seal_trial(path / "trials" / TRIAL, record, path)}
    trials = [{"trial_id": TRIAL, "arm": "a", "hashes": {"agent": plan.get("agent", "h1")}}]
    return JobDir(path=path, manifest=fields, plan={"trials": trials}, records=())


def _pair(tmp_path: Path, **changes: Any) -> list[JobDir]:
    return [_job(tmp_path, "one"), _job(tmp_path, "two", _manifest(**changes))]


def test_matching_jobs_pool_and_a_single_job_is_checked_for_integrity(tmp_path: Path) -> None:
    report = check_pooling(_pair(tmp_path))
    assert (report.unmeasured, report.overrides_used, report.timing_cost_comparable) == (
        (),
        (),
        True,
    )
    assert check_pooling([_job(tmp_path, "solo")]).findings == ()


@pytest.mark.parametrize(
    ("field", "changes"),
    [
        ("toolchain", {"runner": {"commit": "abc", "dirty": False, "toolchain_sha256": "tc2"}}),
        ("pack_recipe", {"recipes": {"pack": "pack2", "graders": {"scb/forge@x": "g1"}}}),
        ("grader_recipe", {"recipes": {"pack": "pack1", "graders": {"scb/forge@x": "g2"}}}),
        ("effective_limits", {"effective_limits": {"agent_s": {"a": 60.0}, "verifier_s": 1500.0}}),
        ("dirty_runner", {"runner": {"commit": "abc", "dirty": True, "toolchain_sha256": "tc1"}}),
    ],
)
def test_pooling_refuses_each_provenance_difference_naming_jobs_and_values(
    tmp_path: Path, field: str, changes: dict[str, Any]
) -> None:
    jobs = _pair(tmp_path, **changes)
    with pytest.raises(PoolingError) as raised:
        check_pooling(jobs)
    assert field in str(raised.value)
    assert "two" in str(raised.value)
    if field != "dirty_runner":
        assert "one=" in str(raised.value)
    lifted = check_pooling(jobs, [field])
    assert lifted.overrides_used == (field,)
    assert lifted.findings[0].message


def test_pooling_refuses_a_different_subject(tmp_path: Path) -> None:
    jobs = [_job(tmp_path, "one"), _job(tmp_path, "two", agent="h2")]
    with pytest.raises(PoolingError, match=r"subject differs.*one=.*h1.*two=.*h2"):
        check_pooling(jobs)
    assert check_pooling(jobs, ["subject"]).overrides_used == ("subject",)


def test_pooling_refuses_a_different_epoch_with_pre_epoch_as_missing(tmp_path: Path) -> None:
    jobs = [_job(tmp_path, "old", {}), _job(tmp_path, "new")]
    with pytest.raises(PoolingError, match=r"mechanics_epoch differs.*old=pre-epoch"):
        check_pooling(jobs)


def test_timing_and_cost_across_target_hardware_are_refused_unless_lifted(tmp_path: Path) -> None:
    other = _manifest()
    other["execution_target"] = {"name": "laptop", "docker": {"os": "linux", "arch": "arm64"}}
    jobs = [_job(tmp_path, "one"), _job(tmp_path, "two", other)]
    with pytest.raises(PoolingError, match="timing_cost differs"):
        check_pooling(jobs)
    report = check_pooling(jobs, ["timing_cost"])
    assert report.timing_cost_comparable is False
    assert report.overrides_used == ("timing_cost",)


def test_pre_epoch_jobs_are_unmeasured_not_refused(tmp_path: Path) -> None:
    jobs = [_job(tmp_path, "old-1", {}), _job(tmp_path, "old-2", {})]
    assert check_pooling(jobs).unmeasured == ("old-1", "old-2")
    assert check_pooling(jobs[:1]).unmeasured == ("old-1",)


@pytest.mark.parametrize("damage", ["edit", "add", "remove-manifest", "edit-record"])
def test_tampering_refuses_even_with_every_override(tmp_path: Path, damage: str) -> None:
    job = _job(tmp_path, "solo")
    trial = job.path / "trials" / TRIAL
    if damage == "edit":
        (trial / "result.json").write_text('{"reward": 1}')
    elif damage == "add":
        (trial / "extra.txt").write_text("x")
    elif damage == "remove-manifest":
        (trial / RECORDS_MANIFEST).unlink()
    else:
        (job.path / "trial-records" / f"{TRIAL}.json").write_text('{"valid": true}')
    with pytest.raises(PoolingError, match="never overridable"):
        check_pooling([job], ["mechanics_epoch", "toolchain", "timing_cost"])


def test_tamper_is_not_an_override_name(tmp_path: Path) -> None:
    with pytest.raises(PoolingError, match="never overridable"):
        check_pooling([_job(tmp_path, "solo")], ["tamper"])
