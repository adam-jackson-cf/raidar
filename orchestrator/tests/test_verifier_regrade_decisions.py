"""Fast verifier boundary and faithful offline regrade decisions."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from harbor.models.trial.paths import TrialPaths

from raidar.grading import closure, offline, records, regrade, verifier
from raidar.grading.grader_image import GraderIdentityError
from raidar.grading.leak import LeakReport, LeakScanError
from raidar.grading.models import CaseGroup, CaseResult, CaseStatus, RunEvidence, VerifierGrade
from raidar.grading.outcomes import RunOutcome, StepGrade
from raidar.grading.records import (
    BundleRef,
    CoverageRef,
    GraderRef,
    SnapshotRef,
    StepRecord,
)
from raidar.grading.solvable import solvable_ref_from_metadata


@pytest.fixture(name="record")
def record_fixture() -> StepRecord:
    grade = VerifierGrade(
        step="checkpoint_1",
        checkpoint="checkpoint_1",
        cases=(
            CaseResult(
                id="test_a::test_ok",
                group=CaseGroup.CORE,
                checkpoint_origin="checkpoint_1",
                status=CaseStatus.PASSED,
            ),
        ),
    )
    return StepRecord(
        step=grade.step,
        checkpoint=grade.checkpoint,
        bundle=BundleRef(root="/bundle", digest="bundle", problem="fixture/adder@1"),
        snapshot=SnapshotRef(
            requested="artifact", used="artifact", digest="tree", stored_path="snapshot"
        ),
        grader=GraderRef(
            image="grader:1", environment_id="sha256:original", image_built=False, timeout_s=2
        ),
        grade=grade,
        summary=grade.summary(),
        coverage=CoverageRef(enabled=False),
        rewards=grade.rewards(),
    )


def _result(record: StepRecord) -> StepGrade:
    return StepGrade(
        record.grade, RunOutcome("grading", record.grade.cases, None, None, (), 0.01), None, None
    )


def _verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **options: Any
) -> verifier.RaidarVerifier:
    monkeypatch.setattr(verifier, "load_bundle", lambda _: SimpleNamespace())
    return verifier.RaidarVerifier(
        oracle_bundle="/bundle",
        docker_context="fixture",
        task=None,
        trial_paths=TrialPaths(trial_dir=tmp_path),
        environment=SimpleNamespace(),
        step_name="checkpoint_1",
        pinned={"grader": {}},
        **options,
    )


def test_unknown_snapshot_source_is_refused_before_bundle_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="snapshot_source"):
        _verifier(tmp_path, monkeypatch, snapshot_source="live")


@pytest.mark.parametrize("required", [True, False])
def test_missing_pre_agent_scan_fails_closed_unless_explicitly_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    required: bool,
) -> None:
    subject = _verifier(tmp_path, monkeypatch, require_pre_agent_check=required)
    if required:
        with pytest.raises(LeakScanError, match="no pre-agent leak check"):
            subject._pre_agent_leak("checkpoint_1", tmp_path)
    else:
        assert subject._pre_agent_leak("checkpoint_1", tmp_path) is None


async def test_download_snapshot_keeps_an_independent_regradable_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _verifier(tmp_path, monkeypatch, snapshot_source="download")

    async def download(**kwargs: Any) -> None:
        target = kwargs["target_dir"]
        target.mkdir(parents=True)
        (target / "app.py").write_text("print(42)\n")

    subject.environment = SimpleNamespace(download_dir_with_exclusions=download)
    record_dir = tmp_path / "evidence"
    snapshot, ref = await subject._snapshot(tmp_path / "work", record_dir)
    retained = tmp_path / ref.stored_path
    assert (retained / "app.py").read_text() == "print(42)\n"
    assert ref.requested == ref.used == "download"
    assert ref.digest == verifier.tree_digest(retained)
    (snapshot / "app.py").write_text("changed\n")
    assert (retained / "app.py").read_text() == "print(42)\n"


async def test_artifact_snapshot_identity_matches_retained_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _verifier(tmp_path, monkeypatch)
    artifact = tmp_path / "app"
    artifact.mkdir()
    (artifact / "app.py").write_text("answer = 42\n")
    monkeypatch.setattr(subject, "_artifact_snapshot", lambda: artifact)
    path, ref = await subject._snapshot(tmp_path / "work", tmp_path / "evidence")
    assert path == artifact
    assert ref.digest == verifier.tree_digest(artifact)
    assert ref.stored_path == "steps/checkpoint_1/artifacts/app"
    assert ref.requested == ref.used == "artifact"


async def test_prepare_sandbox_refuses_a_rebuilt_grader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _verifier(tmp_path, monkeypatch)
    subject.pinned_grader = {
        "image": "grader:1",
        "environment_id": "sha256:original",
        "implementation_sha256": "impl",
    }

    async def image(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(tag="grader:1", built=False)

    async def environment_id() -> str:
        return "sha256:rebuilt"

    monkeypatch.setattr(verifier, "ensure_grader_image", image)
    monkeypatch.setattr(
        verifier, "DockerCopySandbox", lambda **_: SimpleNamespace(environment_id=environment_id)
    )
    monkeypatch.setattr(verifier, "grading_execution_digest", lambda: "impl")
    with pytest.raises(GraderIdentityError, match="environment_id"):
        await subject.prepare_sandbox()


async def test_prepare_sandbox_records_admitted_image_and_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _verifier(tmp_path, monkeypatch, grader_timeout_s=3)
    subject.pinned_grader = {
        "image": "grader:1",
        "environment_id": "sha256:original",
        "implementation_sha256": "impl",
    }

    async def image(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(tag="grader:1", built=True)

    async def environment_id() -> str:
        return "sha256:original"

    monkeypatch.setattr(verifier, "ensure_grader_image", image)
    monkeypatch.setattr(
        verifier, "DockerCopySandbox", lambda **_: SimpleNamespace(environment_id=environment_id)
    )
    monkeypatch.setattr(verifier, "grading_execution_digest", lambda: "impl")
    _, admitted = await subject.prepare_sandbox()
    assert admitted.environment_id == "sha256:original"
    assert admitted.image == "grader:1"
    assert admitted.image_built is True
    assert admitted.timeout_s == 3


def test_verifier_retains_grading_and_isolation_logs(tmp_path: Path) -> None:
    source, target = tmp_path / "source", tmp_path / "retained"
    target.mkdir()
    out = source / "grading" / "out"
    out.mkdir(parents=True)
    (out / "sandbox.log").write_text("submission exited 1\n")
    (out / "secret-isolation.json").write_text('{"absent": true}')
    verifier._keep_logs(source, target)
    assert (target / "grading.log").read_text() == "submission exited 1\n"
    assert json.loads((target / "grading-secret-isolation.json").read_text()) == {"absent": True}
    assert not (target / "coverage.log").exists()


@pytest.mark.parametrize("same", [True, False])
async def test_regrade_reports_case_changes_without_modifying_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record: StepRecord,
    same: bool,
) -> None:
    result = _result(record)
    if not same:
        failed = record.grade.model_copy(
            update={
                "cases": (record.grade.cases[0].model_copy(update={"status": CaseStatus.FAILED}),)
            }
        )
        result = replace(result, grade=failed)
    original = record.model_dump_json()
    bundle = SimpleNamespace()
    monkeypatch.setattr(regrade, "load_step_record", lambda _: record)
    monkeypatch.setattr(regrade, "recorded_bundle", lambda *_: bundle)
    monkeypatch.setattr(regrade, "stored_snapshot", lambda *_: tmp_path / "snapshot")

    async def environment_id() -> str:
        return record.grader.environment_id

    async def factory(_: Any) -> Any:
        return SimpleNamespace(environment_id=environment_id)

    async def grade(*args: Any, **kwargs: Any) -> StepGrade:
        return result

    monkeypatch.setattr(regrade, "grade_step", grade)
    report = await regrade.regrade_step(
        tmp_path,
        tmp_path / "record",
        regrade.RegradeOptions(
            docker_context="fixture",
            work_root=tmp_path,
            sandbox_factory=factory,
        ),
    )
    assert report.match is same
    assert report.regraded["solved"] is same
    assert report.original["solved"] is True
    assert report.differences == (() if same else ("test_a::test_ok: passed -> failed",))
    assert record.model_dump_json() == original


async def test_regrade_requires_at_least_one_retained_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "iterdir", lambda _: iter(()))
    with pytest.raises(regrade.RegradeError, match="no verifier step records"):
        await regrade.regrade_trial(tmp_path, regrade.RegradeOptions("fixture"))


@pytest.mark.parametrize(
    ("store", "final_match", "pre_match", "expected"),
    [
        (None, True, False, True),
        (Path("/store"), True, False, False),
        (Path("/store"), False, True, False),
        (Path("/store"), True, True, True),
    ],
)
def test_offline_match_requires_final_and_retained_pre_fix_agreement(
    monkeypatch: pytest.MonkeyPatch,
    store: Path | None,
    final_match: bool,
    pre_match: bool,
    expected: bool,
) -> None:
    async def final(*_: Any) -> dict[str, Any]:
        return {"match": final_match, "trials": []}

    async def pre_fix(*_: Any) -> dict[str, Any]:
        return {"match": pre_match, "snapshots": []}

    monkeypatch.setattr(offline, "regrade_path", final)
    monkeypatch.setattr(offline, "regrade_pre_fix_path", pre_fix)
    result = offline.regrade(Path("/job"), regrade.RegradeOptions("fixture"), store)
    assert result["match"] is expected
    assert ("pre_fix" in result) is (store is not None)


@pytest.mark.parametrize("metadata", [None, [], {}, {"solvable": None}])
def test_missing_solvable_metadata_never_fabricates_a_verdict(metadata: object) -> None:
    assert solvable_ref_from_metadata(metadata) is None


class _DockerProcess:
    def __init__(self, *, timeout: bool = False) -> None:
        self.returncode = 17
        self.timeout = timeout
        self.killed = False
        self.reaped = False

    async def communicate(self, stdin: bytes | None = None) -> tuple[bytes, None]:
        if self.timeout and not self.killed:
            raise TimeoutError
        self.reaped = True
        return b"image failed\xff", None

    def kill(self) -> None:
        self.killed = True


@pytest.mark.parametrize("timeout", [False, True])
async def test_docker_shell_decodes_failures_and_reaps_timed_out_builds(
    monkeypatch: pytest.MonkeyPatch,
    timeout: bool,
) -> None:
    process = _DockerProcess(timeout=timeout)

    async def spawn(*args: Any, **kwargs: Any) -> _DockerProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    if timeout:
        with pytest.raises(TimeoutError):
            await closure.run_docker(
                "docker", "fixture", "build", stdin=b"FROM base", env={"MODE": "build"}
            )
        assert process.killed
        assert process.reaped
    else:
        code, output = await closure.run_docker("docker", "fixture", "image", "inspect")
        assert code == 17
        assert output == "image failed\ufffd"


def test_pre_agent_scan_is_reloaded_as_leak_evidence(tmp_path: Path) -> None:
    report = LeakReport(clean=False, tests_dir_entries=("/tests/hidden.py",))
    records.write_pre_agent_leak(tmp_path, report)
    recovered = records.load_pre_agent_leak(tmp_path)
    assert recovered is not None
    assert not recovered.clean
    assert recovered.tests_dir_entries == ("/tests/hidden.py",)


def test_step_evidence_preserves_the_case_to_line_fork_handoff(
    tmp_path: Path,
    record: StepRecord,
) -> None:
    evidence = RunEvidence.model_validate(
        {
            "role": "grading",
            "environment": {
                "grader_environment_id": "image",
                "grader_tools_sha256": "tools",
                "grader_interpreter_sha256": "python",
                "submission_interpreter_sha256": "python",
                "platform_sha256": "platform",
                "plugin_sha256": "plugin",
            },
            "corpus": {"corpus_sha256": "corpus", "selected_tests": ["test_a"]},
            "invocation": {"argv": ["pytest", "test_a"], "cwd": "/app"},
            "ledger": [{"node_id": "test_a::test_ok", "phase": "call", "outcome": "passed"}],
        }
    )
    coverage_evidence = evidence.model_copy(
        update={
            "role": "coverage",
        }
    )
    grading = replace(_result(record).grading, evidence=evidence)
    coverage = replace(grading, role="coverage", evidence=coverage_evidence)
    result = replace(_result(record), grading=grading, coverage=coverage)
    records.write_step_evidence(tmp_path, result)
    recovered = json.loads((tmp_path / records.FORK_EVIDENCE_FILE).read_text())
    assert recovered["transition_step"] == "checkpoint_1"
    assert recovered["baseline"]["outcomes"] == [
        {
            "node_id": "test_a::test_ok",
            "phase": "call",
            "outcome": "passed",
            "group_type": "Core",
        }
    ]
    assert recovered["produced"]["corpus"] == recovered["baseline"]["corpus"]
    assert recovered["produced"]["outcomes"] == recovered["baseline"]["outcomes"]


async def test_job_regrade_fails_when_any_trials_case_verdict_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stable, changed = tmp_path / "stable", tmp_path / "changed"
    monkeypatch.setattr(regrade, "trial_dirs", lambda _: [stable, changed])
    monkeypatch.setattr(Path, "iterdir", lambda _: iter([tmp_path / "step"]))
    monkeypatch.setattr(Path, "is_file", lambda _: True)

    async def grade(trial: Path, *args: Any) -> regrade.StepRegrade:
        match = trial == stable
        return regrade.StepRegrade(
            "checkpoint_1",
            match,
            () if match else ("case: passed -> failed",),
            {"solved": True},
            {"solved": match},
        )

    monkeypatch.setattr(regrade, "regrade_step", grade)
    report = await regrade.regrade_path(tmp_path, regrade.RegradeOptions("fixture"))
    assert report["match"] is False
    assert [trial["match"] for trial in report["trials"]] == [True, False]
    failed = report["trials"][1]["steps"][0]
    assert failed["original"]["solved"] is True
    assert failed["regraded"]["solved"] is False
    assert failed["differences"] == ["case: passed -> failed"]
