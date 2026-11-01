"""Offline coverage decisions: when a run is made, reused, refused, stored and compared.

A trial that deferred its coverage pass has it made later from the stored step snapshot. These
tests drive the public production and check entry points over tiny in-``tmp_path`` trial layouts
with an injected sandbox and oracle, and read the stores back through their public readers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from raidar.contract.snapshot import tree_digest
from raidar.grading.coverage import offline
from raidar.grading.coverage.offline import (
    CoverageTarget,
    check_coverage_path,
    check_offline_coverage,
    coverage_targets,
    produce_coverage_path,
    produce_offline_coverage,
    run_offline_coverage,
)
from raidar.grading.coverage.store import (
    SCHEMA_VERSION,
    coverage_path,
    read_offline_coverage,
)
from raidar.grading.models import (
    CanaryRecord,
    CaseGroup,
    CaseResult,
    CaseStatus,
    CorpusEvidence,
    CoverageContext,
    EnvironmentEvidence,
    InfraFailure,
    InvocationEvidence,
    LedgerEntry,
    ParityVerdict,
    RunEvidence,
    VerifierGrade,
)
from raidar.grading.outcomes import RunOutcome
from raidar.grading.records import (
    EVIDENCE_FILE,
    FORK_EVIDENCE_FILE,
    BundleRef,
    CoverageRef,
    GraderRef,
    SnapshotRef,
    StepRecord,
    coverage_identity,
    step_record_dir,
    write_step_record,
)
from raidar.grading.regrade import RegradeError, RegradeOptions

STEP = "checkpoint_2"
ENVIRONMENT = "image-1"


def _case(case_id: str, status: CaseStatus = CaseStatus.PASSED) -> CaseResult:
    return CaseResult(
        id=case_id, group=CaseGroup.CORE, checkpoint_origin="checkpoint_1", status=status
    )


def _evidence(role: str, *, covered: bool = False, ledger_outcome: str = "passed") -> RunEvidence:
    return RunEvidence(
        role=role,
        environment=EnvironmentEvidence(
            grader_environment_id=ENVIRONMENT,
            grader_tools_sha256="tools",
            grader_interpreter_sha256="gi",
            submission_interpreter_sha256="si",
            platform_sha256="platform",
            plugin_sha256="plugin",
        ),
        corpus=CorpusEvidence(corpus_sha256="corpus", selected_tests=("t0", "t1")),
        invocation=InvocationEvidence(argv=("pytest",), cwd="/app"),
        ledger=(LedgerEntry(node_id="t0", phase="call", outcome=ledger_outcome),),
        coverage=(CoverageContext(node_id="t0", path="app.py", executed_lines=(1, 2)),)
        if covered
        else (),
    )


def _outcome(
    role: str = "coverage",
    *,
    cases: tuple[CaseResult, ...] | None = None,
    infra: InfraFailure | None = None,
    evidence: bool = True,
    ledger_outcome: str = "passed",
) -> RunOutcome:
    return RunOutcome(
        role=role,
        cases=(_case("t0"), _case("t1")) if cases is None else cases,
        infra_failure=infra,
        evidence=_evidence(role, covered=role == "coverage", ledger_outcome=ledger_outcome)
        if evidence
        else None,
        timing_sensitive=(),
        seconds=0.0,
    )


def _run_payload(run: RunOutcome) -> dict[str, Any]:
    return {
        "role": run.role,
        "infra_failure": run.infra_failure.model_dump() if run.infra_failure else None,
        "cases": [case.model_dump(mode="json") for case in run.cases],
        "evidence": run.evidence.model_dump(mode="json") if run.evidence else None,
    }


def _record(
    snapshot_digest: str,
    *,
    enabled: bool = False,
    parity: ParityVerdict | None = None,
    differences: tuple[str, ...] = (),
    canary: CanaryRecord | None = None,
    environment: str = ENVIRONMENT,
    timeout_s: float = 30.0,
) -> StepRecord:
    grade = VerifierGrade(step=STEP, checkpoint="cp2", cases=(_case("t0"), _case("t1")))
    return StepRecord(
        step=STEP,
        checkpoint="cp2",
        bundle=BundleRef(root="/bundle", digest="bundle-digest", problem="p"),
        snapshot=SnapshotRef(
            requested="artifact",
            used="artifact",
            digest=snapshot_digest,
            stored_path="artifacts/app",
        ),
        grader=GraderRef(
            image="grader", environment_id=environment, image_built=False, timeout_s=timeout_s
        ),
        grade=grade,
        summary=grade.summary(),
        coverage=CoverageRef(enabled=enabled, parity=parity, differences=differences),
        rewards=grade.rewards(),
        canary=canary or CanaryRecord(),
    )


def _trial(
    root: Path,
    name: str = "trial",
    *,
    grading: RunOutcome | None = None,
    evidence_text: str | None = None,
    **record_args: Any,
) -> CoverageTarget:
    """A trial with one graded step: its stored snapshot, record and grading evidence."""
    trial = root / name
    app = trial / "artifacts" / "app"
    app.mkdir(parents=True)
    (app / "main.py").write_text("print('final')\n")
    record = _record(tree_digest(app), **record_args)
    record_dir = step_record_dir(trial, STEP)
    write_step_record(record_dir, record)
    text = evidence_text
    if text is None:
        payload = {"grading": _run_payload(grading or _outcome("grading")), "coverage": None}
        text = json.dumps(payload)
    (record_dir / EVIDENCE_FILE).write_text(text)
    [target] = coverage_targets(trial)
    return target


@dataclass
class Oracle:
    """Stands in for the sandboxed coverage run; records every run it is asked to make."""

    outcome: RunOutcome = field(default_factory=_outcome)
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(
        self, bundle: Any, step: Any, snapshot: Path, sandbox: Any, work: Path, **kw: Any
    ) -> RunOutcome:
        self.calls.append({"snapshot": snapshot, **kw})
        return self.outcome


class _Bundle:
    def step(self, name: str) -> str:
        return f"spec:{name}"


@pytest.fixture(autouse=True)
def _stable_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("raidar.grading.records.grading_execution_digest", lambda: "impl-1")


@pytest.fixture
def oracle(monkeypatch: pytest.MonkeyPatch) -> Oracle:
    fake = Oracle()
    monkeypatch.setattr(offline, "run_oracle", fake)
    monkeypatch.setattr(offline, "recorded_bundle", lambda record, options: _Bundle())
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


# ---- targets ---------------------------------------------------------------------------


def test_a_trial_without_verifier_records_has_no_targets(tmp_path: Path) -> None:
    assert coverage_targets(tmp_path) == []


def test_a_target_is_deferred_exactly_when_the_trial_ran_no_coverage_pass(tmp_path: Path) -> None:
    deferred = _trial(tmp_path, "a", enabled=False)
    ran = _trial(tmp_path, "b", enabled=True, parity=ParityVerdict(eligible=True))

    assert (deferred.deferred, ran.deferred) == (True, False)


# ---- a single run ----------------------------------------------------------------------


async def test_a_run_is_parity_checked_against_the_trials_grading_run(
    tmp_path: Path, oracle: Oracle
) -> None:
    target = _trial(tmp_path)

    run = await run_offline_coverage(target, _options(tmp_path))

    assert run.parity == ParityVerdict(eligible=True)
    assert run.differences == ()
    assert run.environment_id == ENVIRONMENT
    assert run.infra_failure is None
    assert run.fork is not None
    assert run.fork["transition_step"] == STEP
    assert json.loads(json.dumps(run.fork)) == run.fork


async def test_coverage_that_disagrees_with_grading_is_ineligible_with_the_differences(
    tmp_path: Path, oracle: Oracle
) -> None:
    oracle.outcome = _outcome(cases=(_case("t0"), _case("t1", CaseStatus.FAILED)))

    run = await run_offline_coverage(_trial(tmp_path), _options(tmp_path))

    assert run.parity.eligible is False
    assert "outcome_mismatch" in run.parity.reasons
    assert run.differences == ("t1: grading=passed coverage=failed",)


async def test_a_failed_coverage_run_is_ineligible_and_carries_its_failure(
    tmp_path: Path, oracle: Oracle
) -> None:
    oracle.outcome = _outcome(infra=InfraFailure(code="sandbox_timeout"), evidence=False)

    run = await run_offline_coverage(_trial(tmp_path), _options(tmp_path))

    assert run.parity == ParityVerdict(
        eligible=False, reasons=("coverage_run_failed:sandbox_timeout",)
    )
    assert run.infra_failure == InfraFailure(code="sandbox_timeout")
    assert run.fork is None


async def test_a_step_whose_grading_failed_on_the_submission_gets_no_coverage_run(
    tmp_path: Path, oracle: Oracle
) -> None:
    canary = CanaryRecord(status="passed", trigger="sandbox_timeout")
    target = _trial(tmp_path, canary=canary)

    run = await run_offline_coverage(target, _options(tmp_path))

    assert run.parity.reasons == ("grading_failed_on_submission:sandbox_timeout",)
    assert run.fork is None
    assert oracle.calls == []


async def test_a_step_whose_grading_hit_infrastructure_failure_gets_no_coverage_run(
    tmp_path: Path, oracle: Oracle
) -> None:
    grading = _outcome("grading", infra=InfraFailure(code="sandbox_error"), evidence=False)
    target = _trial(tmp_path, grading=grading)

    run = await run_offline_coverage(target, _options(tmp_path))

    assert run.parity.reasons == ("grading_infra_failure:sandbox_error",)
    assert oracle.calls == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{broken", "unreadable"),
        ("[]", "unreadable"),
        (json.dumps({"coverage": None}), "unreadable"),
        (json.dumps({"grading": None}), "missing"),
    ],
)
async def test_unusable_grading_evidence_is_refused(
    tmp_path: Path, oracle: Oracle, text: str, expected: str
) -> None:
    target = _trial(tmp_path, evidence_text=text)

    with pytest.raises(RegradeError, match=expected):
        await run_offline_coverage(target, _options(tmp_path))

    assert oracle.calls == []


async def test_missing_grading_evidence_is_refused(tmp_path: Path, oracle: Oracle) -> None:
    target = _trial(tmp_path)
    (target.record_dir / EVIDENCE_FILE).unlink()

    with pytest.raises(RegradeError, match="unreadable"):
        await run_offline_coverage(target, _options(tmp_path))


async def test_a_grader_image_unlike_the_trials_is_refused(tmp_path: Path, oracle: Oracle) -> None:
    with pytest.raises(RegradeError, match="environment differs"):
        await run_offline_coverage(_trial(tmp_path), _options(tmp_path, "image-2"))

    assert oracle.calls == []


async def test_a_changed_stored_snapshot_is_refused(tmp_path: Path, oracle: Oracle) -> None:
    target = _trial(tmp_path)
    (target.trial_dir / "artifacts" / "app" / "main.py").write_text("edited\n")

    with pytest.raises(RegradeError, match="changed since grading"):
        await run_offline_coverage(target, _options(tmp_path))


# ---- reusable measurements ------------------------------------------------------------


def _measurements(store: Path) -> list[Path]:
    return sorted((store / "coverage-runs").rglob("*.json"))


async def test_a_measurement_is_made_once_and_reused_by_identical_trials(
    tmp_path: Path, oracle: Oracle
) -> None:
    store = tmp_path / "store"
    first = _trial(tmp_path, "first")
    second = _trial(tmp_path, "second")

    one = await run_offline_coverage(first, _options(tmp_path), store)
    two = await run_offline_coverage(second, _options(tmp_path), store)

    assert len(oracle.calls) == 1
    assert len(_measurements(store)) == 1
    assert (two.parity, two.differences, two.fork) == (one.parity, one.differences, one.fork)


async def test_a_measurement_for_other_execution_facts_is_made_again(
    tmp_path: Path, oracle: Oracle
) -> None:
    store = tmp_path / "store"
    await run_offline_coverage(_trial(tmp_path, "a", timeout_s=10.0), _options(tmp_path), store)
    await run_offline_coverage(_trial(tmp_path, "b", timeout_s=20.0), _options(tmp_path), store)

    assert len(oracle.calls) == 2
    assert len(_measurements(store)) == 2


async def test_a_stored_measurement_bound_to_another_identity_is_not_trusted(
    tmp_path: Path, oracle: Oracle
) -> None:
    store = tmp_path / "store"
    target = _trial(tmp_path)
    await run_offline_coverage(target, _options(tmp_path), store)
    [cached] = _measurements(store)
    payload = json.loads(cached.read_text())
    cached.write_text(json.dumps({**payload, "identity": "someone-else"}))

    await run_offline_coverage(target, _options(tmp_path), store)

    assert len(oracle.calls) == 2
    assert json.loads(cached.read_text())["identity"] == payload["identity"]


async def test_a_failed_measurement_is_never_stored(tmp_path: Path, oracle: Oracle) -> None:
    oracle.outcome = _outcome(infra=InfraFailure(code="sandbox_error"), evidence=False)
    store = tmp_path / "store"

    await run_offline_coverage(_trial(tmp_path), _options(tmp_path), store)

    assert _measurements(store) == []


async def test_a_measurement_without_evidence_is_stored_without_it(
    tmp_path: Path, oracle: Oracle
) -> None:
    grading = _outcome("grading", evidence=False)
    oracle.outcome = _outcome(evidence=False)
    store = tmp_path / "store"

    run = await run_offline_coverage(_trial(tmp_path, grading=grading), _options(tmp_path), store)

    [cached] = _measurements(store)
    assert json.loads(cached.read_text())["evidence"] is None
    assert run.parity.reasons == ("coverage_run_failed:missing",)


# ---- production into the store --------------------------------------------------------------


async def test_a_deferred_step_is_produced_once_into_the_store(
    tmp_path: Path, oracle: Oracle
) -> None:
    target = _trial(tmp_path)
    store = tmp_path / "store"
    record = target.record
    identity = coverage_identity(record, target.record_dir)

    first = await produce_offline_coverage(target, store, _options(tmp_path))
    second = await produce_offline_coverage(target, store, _options(tmp_path))

    path = coverage_path(
        store, record.bundle.digest, STEP, record.snapshot.digest, identity=identity
    )
    assert first["status"] == "produced"
    assert first["path"] == str(path)
    assert first["eligible"] is True
    assert second["status"] == "stored"
    assert second["path"] == first["path"]
    assert len(oracle.calls) == 1
    stored = read_offline_coverage(
        store, record.bundle.digest, STEP, record.snapshot.digest, identity=identity
    )
    assert stored is not None
    assert stored.parity == ParityVerdict(eligible=True)
    assert stored.fork is not None
    assert json.loads(path.read_text())["schema_version"] == SCHEMA_VERSION


async def test_a_run_with_an_infrastructure_failure_is_an_error_and_not_stored(
    tmp_path: Path, oracle: Oracle
) -> None:
    oracle.outcome = _outcome(infra=InfraFailure(code="sandbox_timeout"), evidence=False)
    store = tmp_path / "store"

    entry = await produce_offline_coverage(_trial(tmp_path), store, _options(tmp_path))

    assert entry["status"] == "error"
    assert entry["error"] == "coverage infrastructure failure: sandbox_timeout"
    assert not store.exists()


async def test_production_refuses_what_it_cannot_reproduce_and_stores_nothing(
    tmp_path: Path, oracle: Oracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    changed = _trial(tmp_path, "changed")
    (changed.trial_dir / "artifacts" / "app" / "main.py").write_text("edited\n")
    image = _trial(tmp_path, "image")
    unreadable = _trial(tmp_path, "unreadable", evidence_text="{broken")

    entries = [
        await produce_offline_coverage(changed, store, _options(tmp_path)),
        await produce_offline_coverage(image, store, _options(tmp_path, "image-2")),
        await produce_offline_coverage(unreadable, store, _options(tmp_path)),
    ]

    assert [e["status"] for e in entries] == ["error"] * 3
    assert "changed since grading" in entries[0]["error"]
    assert "environment differs" in entries[1]["error"]
    assert "unreadable" in entries[2]["error"]
    assert oracle.calls == []
    assert not store.exists()

    def refuse(record: StepRecord, options: RegradeOptions) -> _Bundle:
        raise RegradeError("bundle does not match recorded digest")

    monkeypatch.setattr(offline, "recorded_bundle", refuse)
    entry = await produce_offline_coverage(image, store, _options(tmp_path))
    assert "bundle does not match" in entry["error"]


async def test_a_job_produces_only_deferred_steps_in_trial_order(
    tmp_path: Path, oracle: Oracle
) -> None:
    job = tmp_path / "job"
    job.mkdir()
    _trial(job, "a-trial")
    _trial(job, "b-trial", enabled=True, parity=ParityVerdict(eligible=True))
    bad = _trial(job, "c-trial")
    (bad.trial_dir / "artifacts" / "app" / "main.py").write_text("edited\n")
    (job / "not-a-trial").mkdir()

    report = await produce_coverage_path(job, tmp_path / "store", _options(tmp_path), concurrency=2)

    assert report["kind"] == "raidar-offline-coverage-production"
    assert [(Path(e["trial_dir"]).name, e["status"]) for e in report["steps"]] == [
        ("a-trial", "produced"),
        ("c-trial", "error"),
    ]
    assert report["errors"] == 1


async def test_a_path_without_verifier_records_produces_nothing(
    tmp_path: Path, oracle: Oracle
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    report = await produce_coverage_path(empty, tmp_path / "store", _options(tmp_path))

    assert report == {"kind": "raidar-offline-coverage-production", "steps": [], "errors": 0}


# ---- the check ---------------------------------------------------------------------------


async def _trial_with_own_pass(tmp_path: Path, oracle: Oracle) -> CoverageTarget:
    """A trial whose in-trial coverage pass recorded exactly what the offline run reproduces."""
    probe = _trial(tmp_path, "probe")
    run = await run_offline_coverage(probe, _options(tmp_path))
    target = _trial(tmp_path, "trial", enabled=True, parity=run.parity, differences=run.differences)
    (target.record_dir / FORK_EVIDENCE_FILE).write_text(json.dumps(run.fork))
    return target


async def test_the_check_matches_a_reproducible_in_trial_pass(
    tmp_path: Path, oracle: Oracle
) -> None:
    target = await _trial_with_own_pass(tmp_path, oracle)

    entry = await check_offline_coverage(target, None, _options(tmp_path))

    assert entry["status"] == "match"
    assert entry["against"] == "trial"
    assert entry["differences"] == []
    assert entry["eligible"] is True


async def test_the_check_reports_every_way_the_offline_run_differs(
    tmp_path: Path, oracle: Oracle
) -> None:
    target = await _trial_with_own_pass(tmp_path, oracle)
    oracle.outcome = _outcome(
        cases=(_case("t0"), _case("t1", CaseStatus.FAILED)), ledger_outcome="failed"
    )

    entry = await check_offline_coverage(target, None, _options(tmp_path))

    assert entry["status"] == "mismatch"
    kinds = [d.split(":")[0] for d in entry["differences"]]
    assert kinds == ["parity", "parity differences", FORK_EVIDENCE_FILE]
    assert entry["differences"][2].endswith("differs in produced")
    assert entry["eligible"] is False


@pytest.mark.parametrize(
    ("fork", "expected"),
    [(None, "absent -> present"), ("present", "differs in baseline")],
)
async def test_the_check_names_how_the_g_input_changed(
    tmp_path: Path, oracle: Oracle, fork: str | None, expected: str
) -> None:
    target = await _trial_with_own_pass(tmp_path, oracle)
    path = target.record_dir / FORK_EVIDENCE_FILE
    if fork is None:
        path.unlink()
    else:
        path.write_text(json.dumps({**json.loads(path.read_text()), "baseline": {}}))

    entry = await check_offline_coverage(target, None, _options(tmp_path))

    assert entry["differences"] == [f"{FORK_EVIDENCE_FILE}: {expected}"]


async def test_the_check_notices_a_g_input_the_offline_run_no_longer_produces(
    tmp_path: Path, oracle: Oracle
) -> None:
    target = await _trial_with_own_pass(tmp_path, oracle)
    oracle.outcome = _outcome(infra=InfraFailure(code="sandbox_error"), evidence=False)

    entry = await check_offline_coverage(target, None, _options(tmp_path))

    assert f"{FORK_EVIDENCE_FILE}: present -> absent" in entry["differences"]


async def test_a_deferred_step_is_checked_against_the_store_once_produced(
    tmp_path: Path, oracle: Oracle
) -> None:
    target = _trial(tmp_path)
    store = tmp_path / "store"

    before = await check_offline_coverage(target, store, _options(tmp_path))
    no_store = await check_offline_coverage(target, None, _options(tmp_path))
    await produce_offline_coverage(target, store, _options(tmp_path))
    after = await check_offline_coverage(target, store, _options(tmp_path))
    oracle.outcome = _outcome(cases=(_case("t0"), _case("t1", CaseStatus.FAILED)))
    drifted = await check_offline_coverage(target, store, _options(tmp_path))

    assert before["status"] == no_store["status"] == "not_produced"
    assert before["against"] == "store"
    assert after["status"] == "match"
    assert after["against"] == "store"
    assert drifted["status"] == "mismatch"


async def test_the_check_reports_a_run_it_cannot_make_as_an_error(
    tmp_path: Path, oracle: Oracle
) -> None:
    target = await _trial_with_own_pass(tmp_path, oracle)

    entry = await check_offline_coverage(target, None, _options(tmp_path, "image-2"))

    assert entry["status"] == "error"
    assert entry["against"] == "trial"
    assert "environment differs" in entry["error"]


async def test_a_job_check_matches_only_when_every_step_matches(
    tmp_path: Path, oracle: Oracle
) -> None:
    job = tmp_path / "job"
    job.mkdir()
    probe = _trial(tmp_path, "probe")
    run = await run_offline_coverage(probe, _options(tmp_path))
    own = _trial(job, "a-trial", enabled=True, parity=run.parity, differences=run.differences)
    (own.record_dir / FORK_EVIDENCE_FILE).write_text(json.dumps(run.fork))

    matching = await check_coverage_path(job, None, _options(tmp_path))
    _trial(job, "b-trial")
    mixed = await check_coverage_path(job, None, _options(tmp_path), concurrency=2)

    assert matching["kind"] == "raidar-offline-coverage-check"
    assert matching["match"] is True
    assert mixed["match"] is False
    assert [e["status"] for e in mixed["steps"]] == ["match", "not_produced"]


async def test_checking_a_path_without_verifier_records_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(RegradeError, match="no raidar-oracle/ records"):
        await check_coverage_path(empty, None, _options(tmp_path))


async def test_a_trial_with_no_graded_steps_does_not_match(tmp_path: Path) -> None:
    trial = tmp_path / "trial"
    (trial / "raidar-oracle").mkdir(parents=True)

    report = await check_coverage_path(trial, None, _options(tmp_path))

    assert report == {"kind": "raidar-offline-coverage-check", "match": False, "steps": []}


# ---- the store's reading contract --------------------------------------------------------


def _stored(store: Path, **override: Any) -> None:
    payload: dict[str, Any] = {
        "kind": "raidar-offline-coverage",
        "schema_version": SCHEMA_VERSION,
        "step": "s",
        "snapshot_digest": "snap",
        "identity": "id",
        "parity": {"eligible": False, "reasons": ["outcome_mismatch"]},
        "differences": ["t1: grading=passed coverage=failed"],
        "fork": {"transition_step": "s"},
    }
    payload.update(override)
    path = coverage_path(store, "bundle", "s", "snap", identity="id")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload))


def _read(store: Path, identity: str = "id") -> Any:
    return read_offline_coverage(store, "bundle", "s", "snap", identity=identity)


def test_a_stored_run_is_read_back_whole(tmp_path: Path) -> None:
    _stored(tmp_path)

    stored = _read(tmp_path)

    assert stored.parity == ParityVerdict(eligible=False, reasons=("outcome_mismatch",))
    assert stored.differences == ("t1: grading=passed coverage=failed",)
    assert stored.fork == {"transition_step": "s"}


def test_a_stored_run_without_differences_or_a_dict_fork_reads_as_empty(tmp_path: Path) -> None:
    _stored(tmp_path, differences=None, fork="nope")

    stored = _read(tmp_path)

    assert stored.differences == ()
    assert stored.fork is None


def test_the_legacy_unbound_path_has_no_identity_directory(tmp_path: Path) -> None:
    bound = coverage_path(tmp_path, "b", "s", "snap", identity="id")
    legacy = coverage_path(tmp_path, "b", "s", "snap")

    assert bound == legacy.parent / "id" / "snap.json"


def test_an_unbound_read_is_never_satisfied(tmp_path: Path) -> None:
    legacy = coverage_path(tmp_path, "bundle", "s", "snap")
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"kind": "raidar-offline-coverage", "identity": ""}))

    assert _read(tmp_path, identity="") is None


@pytest.mark.parametrize(
    "override",
    [
        {"kind": "other"},
        {"schema_version": SCHEMA_VERSION - 1},
        {"step": "other"},
        {"snapshot_digest": "other"},
        {"identity": "other"},
    ],
)
def test_a_stored_run_for_another_snapshot_or_schema_is_not_read(
    tmp_path: Path, override: dict[str, Any]
) -> None:
    _stored(tmp_path, **override)

    assert _read(tmp_path) is None


def test_a_run_that_was_never_stored_is_absent(tmp_path: Path) -> None:
    assert _read(tmp_path) is None


@pytest.mark.parametrize("raw", [b"{nope", b"\xff\xfe", b"[]"])
def test_an_unreadable_stored_run_is_not_read(tmp_path: Path, raw: bytes) -> None:
    path = coverage_path(tmp_path, "bundle", "s", "snap", identity="id")
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)

    assert _read(tmp_path) is None
