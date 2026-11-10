"""Authenticate new observations without refetching immutable trusted baselines."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
RULE = "TEST.budget.fast-tier-time"
type Json = dict[str, Any]


@pytest.fixture
def evidence() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "budget_provenance_evidence", ROOT / "scripts/checks/test_budget_evidence.py"
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _report(job: str = "21") -> Json:
    return {
        "revision": "measured-revision",
        "invocationId": f"measurement-{job}",
        "wallSeconds": 12.0,
        "cases": {"test_example.py::test_case": {"phases": {"call": 0.03}}},
        "measurement": {"runId": "10", "jobId": job, "successful": True},
    }


def _unexpected_network(*_args: Any) -> None:
    pytest.fail("trusted unchanged observations must not require retained artifacts")


def test_trusted_records_survive_artifact_expiry_without_network(
    evidence: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted = {"10-21.json": _report(), "10-22.json": _report("22")}
    monkeypatch.setattr(evidence, "_measurement_artifacts", _unexpected_network)
    monkeypatch.setattr(evidence, "_repository", _unexpected_network)
    evidence.authenticate_baselines(ROOT, copy.deepcopy(trusted), trusted)
    # Dropping a historical observation is not mutation of retained evidence.
    evidence.authenticate_baselines(ROOT, {"10-21.json": _report()}, trusted)
    evidence.authenticate_baselines(ROOT, {}, trusted)


@pytest.mark.parametrize("field", ["wallSeconds", "cases", "measurement"])
def test_retained_trusted_record_cannot_be_rewritten(
    evidence: ModuleType, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    report = _report()
    changed = copy.deepcopy(report)
    changed[field] = "fabricated replacement"
    monkeypatch.setattr(evidence, "_measurement_artifacts", _unexpected_network)
    with pytest.raises(ValueError, match=f"{RULE}.*immutable baseline changed"):
        evidence.authenticate_baselines(
            ROOT, {"10-21.json": changed}, {"10-21.json": report}, "owner/repository"
        )


@pytest.mark.parametrize("available", [False, True])
def test_new_record_must_equal_external_observations(
    evidence: ModuleType, monkeypatch: pytest.MonkeyPatch, available: bool
) -> None:
    proposed = _report()
    observed = _report()
    observed["wallSeconds"] = 99.0

    def download(*_args: Any) -> list[tuple[Json, Json]]:
        return [(observed, {})] if available else []

    monkeypatch.setattr(evidence, "_measurement_artifacts", download)
    with pytest.raises(ValueError, match=f"{RULE}.*differs from GitHub"):
        evidence.authenticate_baselines(ROOT, {"10-21.json": proposed}, {}, "owner/repository")


def test_authentic_new_jobs_share_one_external_run_download(
    evidence: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports = [_report(), _report("22")]
    proposed = {"10-21.json": reports[0], "10-22.json": reports[1]}
    original = copy.deepcopy(proposed)
    downloads: list[tuple[str, int]] = []

    def download(_root: Path, repository: str, run_id: int) -> list[tuple[Json, Json]]:
        downloads.append((repository, run_id))
        return [(copy.deepcopy(report), {}) for report in reports]

    monkeypatch.setattr(evidence, "_measurement_artifacts", download)
    monkeypatch.setattr(evidence, "_repository", lambda _root: "owner/repository")
    evidence.authenticate_baselines(ROOT, proposed, {})
    assert proposed == original
    assert downloads == [("owner/repository", 10)]


@pytest.mark.parametrize(
    ("filename", "run_id", "job_id"),
    [("renamed.json", "10", "21"), ("bad-21.json", "bad", "21"), ("10-bad.json", "10", "bad")],
)
def test_new_record_identity_must_bind_its_immutable_filename(
    evidence: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    run_id: str,
    job_id: str,
) -> None:
    report = _report()
    report["measurement"].update(runId=run_id, jobId=job_id)
    monkeypatch.setattr(evidence, "_measurement_artifacts", _unexpected_network)
    with pytest.raises(ValueError, match=f"{RULE}.*identity is invalid"):
        evidence.authenticate_baselines(ROOT, {filename: report}, {}, "owner/repository")


def test_unavailable_external_evidence_cannot_authorize_new_baseline(
    evidence: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(*_args: Any) -> None:
        raise OSError("artifact service unavailable")

    monkeypatch.setattr(evidence, "_measurement_artifacts", unavailable)
    with pytest.raises(OSError, match="artifact service unavailable"):
        evidence.authenticate_baselines(ROOT, {"10-21.json": _report()}, {}, "owner/repository")


def test_trusted_snapshot_reads_baseline_bytes_from_base_not_worktree(
    evidence: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _report()
    directory = ".omp/test-budgets/baselines/"

    def git(_root: Path, *command: str) -> bytes:
        if command == ("git", "ls-tree", "-r", "--name-only", "trusted-sha", "--", directory):
            return f"{directory}10-21.json\n{directory}ignored.txt\n".encode()
        if command == ("git", "show", f"trusted-sha:{directory}10-21.json"):
            return json.dumps(report).encode()
        raise AssertionError(f"unexpected git operation: {command}")

    monkeypatch.setattr(evidence, "_run", git)
    assert evidence._baselines_at_base(ROOT, "trusted-sha") == {"10-21.json": report}
