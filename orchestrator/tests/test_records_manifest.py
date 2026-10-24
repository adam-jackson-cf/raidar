"""Records manifest: every file, symlink and linked directory under a trial is sealed."""

from __future__ import annotations

from pathlib import Path

from raidar.execution.health.records_manifest import seal_trial, tamper_findings


def _trial(tmp_path: Path) -> tuple[Path, Path, Path]:
    job = tmp_path / "job"
    trial = job / "trials" / "t1"
    (trial / "evidence").mkdir(parents=True)
    (trial / "evidence" / "a.txt").write_text("a")
    record = job / "trial-records" / "t1.json"
    record.parent.mkdir()
    record.write_text("{}")
    return job, trial, record


def test_retargeting_a_symlinked_directory_is_a_tamper_finding(tmp_path: Path) -> None:
    job, trial, record = _trial(tmp_path)
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    (trial / "linked").symlink_to(tmp_path / "x", target_is_directory=True)
    digest = seal_trial(trial, record, job)
    assert tamper_findings(trial, record, job, digest) == []
    (trial / "linked").unlink()
    (trial / "linked").symlink_to(tmp_path / "y", target_is_directory=True)
    assert tamper_findings(trial, record, job, digest) == ["trials/t1/linked changed"]


def test_an_edited_added_or_removed_file_is_a_tamper_finding(tmp_path: Path) -> None:
    job, trial, record = _trial(tmp_path)
    digest = seal_trial(trial, record, job)
    (trial / "evidence" / "a.txt").write_text("edited")
    (trial / "evidence" / "b.txt").write_text("b")
    assert tamper_findings(trial, record, job, digest) == [
        "trials/t1/evidence/a.txt changed",
        "trials/t1/evidence/b.txt added",
    ]
    assert tamper_findings(trial, record, job, None) == ["no records manifest digest in job.json"]
