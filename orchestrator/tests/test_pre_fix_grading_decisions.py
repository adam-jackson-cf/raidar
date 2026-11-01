"""Pre-fix snapshot grading decisions: what is graded, cached, refused and compared.

The retained pre-fix trees that ``gate/1`` records are graded offline into the Assessment store.
These tests drive the public grading entry points over tiny in-``tmp_path`` trial layouts with an
injected sandbox and grader, and read the store back through its public reader.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raidar.contract.snapshot import tree_digest
from raidar.grading.models import (
    CaseGroup,
    CaseResult,
    CaseStatus,
    InfraFailure,
    VerifierGrade,
)
from raidar.grading.pre_fix import grade as pre_fix
from raidar.grading.pre_fix.grade import (
    grade_pre_fix_path,
    grade_pre_fix_snapshot,
    pre_fix_snapshots,
    regrade_pre_fix_path,
)
from raidar.grading.pre_fix.store import (
    GRADE_KIND,
    SCHEMA_VERSION,
    grade_path,
    read_pre_fix_grade,
)
from raidar.grading.records import (
    BundleRef,
    CoverageRef,
    GraderRef,
    SnapshotRef,
    StepRecord,
    grading_identity,
    step_record_dir,
    write_step_record,
)
from raidar.grading.regrade import RegradeError, RegradeOptions

STEP = "checkpoint_2"
RETAINED = "gate/pre_fix_1/app"
ENVIRONMENT = "image-1"


def _case(case_id: str, status: CaseStatus) -> CaseResult:
    return CaseResult(
        id=case_id, group=CaseGroup.CORE, checkpoint_origin="checkpoint_1", status=status
    )


def _grade(*statuses: CaseStatus, infra: InfraFailure | None = None) -> VerifierGrade:
    cases = tuple(_case(f"t{i}", status) for i, status in enumerate(statuses))
    return VerifierGrade(step=STEP, checkpoint="cp2", cases=cases, infra_failure=infra)


def _record(
    step: str = STEP, *, environment: str = ENVIRONMENT, timeout_s: float = 30.0
) -> StepRecord:
    grade = _grade(CaseStatus.PASSED)
    return StepRecord(
        step=step,
        checkpoint="cp2",
        bundle=BundleRef(root="/bundle", digest="bundle-digest", problem="p"),
        snapshot=SnapshotRef(
            requested="artifact", used="artifact", digest="final", stored_path="artifacts/app"
        ),
        grader=GraderRef(
            image="grader", environment_id=environment, image_built=False, timeout_s=timeout_s
        ),
        grade=grade,
        summary=grade.summary(),
        coverage=CoverageRef(enabled=False),
        rewards=grade.rewards(),
    )


@dataclass
class Grader:
    """Stands in for the verifier run; records every grading it is asked for."""

    grade: VerifierGrade = field(default_factory=lambda: _grade(CaseStatus.PASSED))
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(
        self, bundle: Any, step: str, path: Path, sandbox: Any, work: Path, **kw: Any
    ) -> SimpleNamespace:
        self.calls.append({"step": step, "path": path, "work_exists": work.is_dir(), **kw})
        return SimpleNamespace(grade=self.grade)


@pytest.fixture(autouse=True)
def _stable_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("raidar.grading.records.grading_execution_digest", lambda: "impl-1")


@pytest.fixture
def grader(monkeypatch: pytest.MonkeyPatch) -> Grader:
    fake = Grader()
    monkeypatch.setattr(pre_fix, "grade_step", fake)
    monkeypatch.setattr(pre_fix, "recorded_bundle", lambda record, options: "bundle")
    return fake


def _options(tmp_path: Path, environment: str = ENVIRONMENT) -> RegradeOptions:
    class Sandbox:
        async def environment_id(self) -> str:
            return environment

    async def factory(bundle: Any) -> Sandbox:
        return Sandbox()

    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return RegradeOptions(docker_context="ctx", work_root=work, sandbox_factory=factory)


def _trial(
    root: Path,
    name: str = "trial",
    *,
    record: StepRecord | None = None,
    entries: list[Any] | None = None,
) -> tuple[Path, StepRecord, str]:
    """A trial with one graded step, a retained pre-fix tree and its ``gate/1`` entry."""
    record = record or _record()
    trial = root / name
    write_step_record(step_record_dir(trial, record.step), record)
    agent = trial / "steps" / record.step / "agent"
    app = agent / RETAINED
    app.mkdir(parents=True)
    (app / "main.py").write_text("print('before the fix')\n")
    digest = tree_digest(app)
    recorded = (
        entries if entries is not None else [{"round": 1, "path": RETAINED, "tree_digest": digest}]
    )
    (agent / "workflow").mkdir()
    (agent / "workflow" / "workflow.json").write_text(
        json.dumps({"gate/1": {"pre_fix_snapshots": recorded}})
    )
    return trial, record, digest


def _workflow(trial: Path) -> Path:
    return trial / "steps" / STEP / "agent" / "workflow" / "workflow.json"


# ---- discovery -------------------------------------------------------------------------


def test_a_trial_without_verifier_records_has_no_snapshots(tmp_path: Path) -> None:
    assert pre_fix_snapshots(tmp_path) == []


def test_only_well_formed_entries_are_snapshots(tmp_path: Path) -> None:
    entries = [
        "not-an-entry",
        {"round": 1, "path": RETAINED},
        {"round": 1, "tree_digest": "d"},
        {"tree_digest": 5, "path": RETAINED},
        {"path": RETAINED, "tree_digest": "keep"},
    ]
    trial, record, _ = _trial(tmp_path, entries=entries)

    found = pre_fix_snapshots(trial)

    assert [(s.step, s.round, s.digest, s.contained) for s in found] == [(STEP, 0, "keep", True)]
    assert found[0].path == trial / "steps" / STEP / "agent" / RETAINED
    assert found[0].record == record


@pytest.mark.parametrize(
    "content",
    [
        None,
        "{broken",
        "[]",
        json.dumps({"gate/1": "nope"}),
        json.dumps({"gate/1": {"pre_fix_snapshots": None}}),
    ],
)
def test_an_unusable_workflow_record_means_no_snapshot(tmp_path: Path, content: str | None) -> None:
    trial, _, _ = _trial(tmp_path)
    if content is None:
        _workflow(trial).unlink()
    else:
        _workflow(trial).write_text(content)

    assert pre_fix_snapshots(trial) == []


def test_a_recorded_path_leaving_the_agent_logs_is_marked_uncontained(tmp_path: Path) -> None:
    entries = [
        {"round": 1, "path": "../../../outside", "tree_digest": "d"},
        {"round": 2, "path": RETAINED, "tree_digest": "d"},
    ]
    trial, _, _ = _trial(tmp_path, entries=entries)

    assert [s.contained for s in pre_fix_snapshots(trial)] == [False, True]


# ---- grading into the store ----------------------------------------------------------------


async def test_a_snapshot_is_graded_once_with_the_recorded_settings(
    tmp_path: Path, grader: Grader
) -> None:
    trial, record, digest = _trial(tmp_path)
    store = tmp_path / "store"
    [snapshot] = pre_fix_snapshots(trial)

    first = await grade_pre_fix_snapshot(snapshot, store, _options(tmp_path))
    second = await grade_pre_fix_snapshot(snapshot, store, _options(tmp_path))

    identity = grading_identity(record.grader)
    target = grade_path(store, "bundle-digest", STEP, digest, identity=identity)
    assert first["status"] == "graded"
    assert first["path"] == str(target)
    assert first["summary"]["solved"] is True
    assert second["status"] == "stored"
    assert second["path"] == str(target)
    assert len(grader.calls) == 1
    stored = read_pre_fix_grade(store, "bundle-digest", STEP, digest, identity=identity)
    assert stored == _grade(CaseStatus.PASSED)
    payload = json.loads(target.read_text())
    assert payload["round"] == 1
    assert payload["grader_environment_id"] == ENVIRONMENT
    assert payload["timeout_s"] == 30.0


async def test_a_different_timeout_is_a_different_stored_grade(
    tmp_path: Path, grader: Grader
) -> None:
    store = tmp_path / "store"
    fast, _, _ = _trial(tmp_path, "fast", record=_record(timeout_s=10.0))
    slow, _, _ = _trial(tmp_path, "slow", record=_record(timeout_s=60.0))

    reports = [await grade_pre_fix_path(trial, store, _options(tmp_path)) for trial in (fast, slow)]

    paths = {report["snapshots"][0]["path"] for report in reports}
    assert len(paths) == 2


async def test_a_corrupt_stored_grade_is_graded_again_and_replaced(
    tmp_path: Path, grader: Grader
) -> None:
    trial, record, digest = _trial(tmp_path)
    store = tmp_path / "store"
    [snapshot] = pre_fix_snapshots(trial)
    identity = grading_identity(record.grader)
    target = grade_path(store, "bundle-digest", STEP, digest, identity=identity)
    target.parent.mkdir(parents=True)
    target.write_text("{truncated")

    entry = await grade_pre_fix_snapshot(snapshot, store, _options(tmp_path))

    assert entry["status"] == "graded"
    assert read_pre_fix_grade(store, "bundle-digest", STEP, digest, identity=identity) is not None


@pytest.mark.parametrize("escape", ["parent", "absolute"])
async def test_a_recorded_path_leaving_the_agent_logs_is_never_graded(
    tmp_path: Path, grader: Grader, escape: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.py").write_text("x = 1\n")
    path = "../../../../outside" if escape == "parent" else str(outside)
    trial, _, _ = _trial(
        tmp_path, entries=[{"round": 1, "path": path, "tree_digest": tree_digest(outside)}]
    )

    report = await grade_pre_fix_path(trial, tmp_path / "store", _options(tmp_path))

    assert report["errors"] == 1
    [entry] = report["snapshots"]
    assert entry["status"] == "error"
    assert "leaves the agent logs" in entry["error"]
    assert grader.calls == []
    assert not (tmp_path / "store").exists()


async def test_a_missing_or_changed_tree_is_an_error_never_a_grade(
    tmp_path: Path, grader: Grader
) -> None:
    changed, _, _ = _trial(tmp_path, "changed")
    (changed / "steps" / STEP / "agent" / RETAINED / "main.py").write_text("edited\n")
    missing, _, _ = _trial(tmp_path, "missing")
    (missing / "steps" / STEP / "agent" / RETAINED / "main.py").unlink()
    (missing / "steps" / STEP / "agent" / RETAINED).rmdir()
    store = tmp_path / "store"

    reports = [
        await grade_pre_fix_path(trial, store, _options(tmp_path)) for trial in (changed, missing)
    ]

    for report in reports:
        assert report["errors"] == 1
        assert "changed since it was recorded" in report["snapshots"][0]["error"]
    assert grader.calls == []
    assert not store.exists()


async def test_a_bundle_that_does_not_match_its_record_is_an_error(
    tmp_path: Path, grader: Grader, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(record: StepRecord, options: RegradeOptions) -> str:
        raise RegradeError("bundle does not match recorded digest")

    monkeypatch.setattr(pre_fix, "recorded_bundle", refuse)
    trial, _, _ = _trial(tmp_path)

    report = await grade_pre_fix_path(trial, tmp_path / "store", _options(tmp_path))

    assert report["snapshots"][0]["status"] == "error"
    assert "bundle does not match" in report["snapshots"][0]["error"]
    assert grader.calls == []


async def test_a_grader_image_unlike_the_trials_is_refused(tmp_path: Path, grader: Grader) -> None:
    trial, _, _ = _trial(tmp_path)

    report = await grade_pre_fix_path(trial, tmp_path / "store", _options(tmp_path, "image-2"))

    assert report["errors"] == 1
    assert "environment differs" in report["snapshots"][0]["error"]
    assert grader.calls == []
    assert not (tmp_path / "store").exists()


async def test_an_infrastructure_failure_is_an_error_and_nothing_is_stored(
    tmp_path: Path, grader: Grader
) -> None:
    grader.grade = _grade(infra=InfraFailure(code="sandbox_timeout"))
    trial, _, _ = _trial(tmp_path)

    report = await grade_pre_fix_path(trial, tmp_path / "store", _options(tmp_path))

    assert report["snapshots"][0]["status"] == "error"
    assert report["snapshots"][0]["error"] == "pre-fix infrastructure failure: sandbox_timeout"
    assert not (tmp_path / "store").exists()


async def test_a_job_grades_every_trial_in_order_and_counts_errors(
    tmp_path: Path, grader: Grader
) -> None:
    job = tmp_path / "job"
    job.mkdir()
    ok, _, _ = _trial(job, "a-trial")
    bad, _, _ = _trial(job, "b-trial")
    (bad / "steps" / STEP / "agent" / RETAINED / "main.py").write_text("edited\n")
    (job / "not-a-trial").mkdir()

    report = await grade_pre_fix_path(job, tmp_path / "store", _options(tmp_path), concurrency=2)

    assert report["kind"] == "raidar-pre-fix-grading"
    assert [(Path(e["trial_dir"]).name, e["status"]) for e in report["snapshots"]] == [
        ("a-trial", "graded"),
        ("b-trial", "error"),
    ]
    assert report["errors"] == 1


async def test_a_path_without_verifier_records_grades_nothing(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    report = await grade_pre_fix_path(empty, tmp_path / "store", _options(tmp_path))

    assert report == {"kind": "raidar-pre-fix-grading", "snapshots": [], "errors": 0}


# ---- regrade against the stored grade -------------------------------------------------------


async def test_regrade_matches_a_reproduced_grade_and_flags_a_changed_one(
    tmp_path: Path, grader: Grader
) -> None:
    trial, _, _ = _trial(tmp_path)
    store, options = tmp_path / "store", _options(tmp_path)
    await grade_pre_fix_path(trial, store, options)

    same = await regrade_pre_fix_path(trial, store, options)
    grader.grade = _grade(CaseStatus.FAILED)
    differs = await regrade_pre_fix_path(trial, store, options)
    grader.grade = _grade(CaseStatus.PASSED, infra=InfraFailure(code="sandbox_error"))
    infra = await regrade_pre_fix_path(trial, store, options)

    assert same["kind"] == "raidar-pre-fix-regrade"
    assert same["match"] is True
    assert [e["status"] for e in same["snapshots"]] == ["match"]
    assert differs["match"] is False
    assert [e["status"] for e in differs["snapshots"]] == ["mismatch"]
    assert [e["status"] for e in infra["snapshots"]] == ["mismatch"]


async def test_regrade_reports_snapshots_that_were_never_graded(
    tmp_path: Path, grader: Grader
) -> None:
    trial, _, _ = _trial(tmp_path)

    report = await regrade_pre_fix_path(trial, tmp_path / "store", _options(tmp_path))

    assert report["match"] is False
    assert [e["status"] for e in report["snapshots"]] == ["not_graded"]
    assert grader.calls == []


async def test_regrade_with_no_trials_is_vacuously_matching(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    report = await regrade_pre_fix_path(empty, tmp_path / "store", _options(tmp_path))

    assert report == {"kind": "raidar-pre-fix-regrade", "match": True, "snapshots": []}


@pytest.mark.parametrize("damage", ["missing", "changed", "uncontained", "environment"])
async def test_regrade_refuses_a_snapshot_it_cannot_reproduce_faithfully(
    tmp_path: Path, grader: Grader, damage: str
) -> None:
    trial, record, digest = _trial(tmp_path)
    store, options = tmp_path / "store", _options(tmp_path)
    await grade_pre_fix_path(trial, store, options)
    app = trial / "steps" / STEP / "agent" / RETAINED
    expected = {
        "missing": "snapshot missing",
        "changed": "changed since it was recorded",
        "uncontained": "leaves the agent logs",
        "environment": "environment differs",
    }[damage]
    if damage == "missing":
        (app / "main.py").unlink()
        app.rmdir()
    elif damage == "changed":
        (app / "main.py").write_text("edited\n")
    elif damage == "uncontained":
        _workflow(trial).write_text(
            json.dumps(
                {"gate/1": {"pre_fix_snapshots": [{"path": "../../x", "tree_digest": digest}]}}
            )
        )
    else:
        options = _options(tmp_path, "image-2")
    calls_before = len(grader.calls)

    report = await regrade_pre_fix_path(trial, store, options)

    [entry] = report["snapshots"]
    assert entry["status"] == "error"
    assert expected in entry["error"]
    assert report["match"] is False
    assert len(grader.calls) == calls_before


# ---- the store's reading contract ----------------------------------------------------------


def _stored_payload(
    snapshot: str = "snap", step: str = "s", identity: str = "id"
) -> dict[str, Any]:
    return {
        "kind": GRADE_KIND,
        "schema_version": SCHEMA_VERSION,
        "identity": identity,
        "step": step,
        "snapshot_digest": snapshot,
        "grade": _grade(CaseStatus.PASSED).model_dump(mode="json"),
    }


def _write_stored(store: Path, payload: Any, raw: bytes | None = None) -> None:
    target = grade_path(store, "bundle", "s", "snap", identity="id")
    target.parent.mkdir(parents=True)
    target.write_bytes(raw if raw is not None else json.dumps(payload).encode())


def _read(store: Path, identity: str = "id") -> VerifierGrade | None:
    return read_pre_fix_grade(store, "bundle", "s", "snap", identity=identity)


def test_a_well_formed_stored_grade_is_read_back(tmp_path: Path) -> None:
    _write_stored(tmp_path, _stored_payload())

    assert _read(tmp_path) == _grade(CaseStatus.PASSED)


def test_an_unbound_read_never_touches_the_store(tmp_path: Path) -> None:
    _write_stored(tmp_path, _stored_payload())

    assert _read(tmp_path, identity="") is None


def test_a_grade_that_was_never_stored_is_absent(tmp_path: Path) -> None:
    assert _read(tmp_path) is None


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {**_stored_payload(), "kind": "other"},
        {**_stored_payload(), "snapshot_digest": "other"},
        {**_stored_payload(), "step": "other"},
        {**_stored_payload(), "identity": "other"},
        {**_stored_payload(), "schema_version": SCHEMA_VERSION - 1},
    ],
)
def test_a_stored_grade_that_is_not_for_this_snapshot_is_not_read(
    tmp_path: Path, payload: Any
) -> None:
    _write_stored(tmp_path, payload)

    assert _read(tmp_path) is None


@pytest.mark.parametrize("raw", [b"{nope", b"\xff\xfe"])
def test_an_unreadable_stored_grade_is_not_read(tmp_path: Path, raw: bytes) -> None:
    _write_stored(tmp_path, None, raw)

    assert _read(tmp_path) is None
