"""The verifier's coverage pass made offline from stored step snapshots (iteration 3b.2).

A trial graded with ``coverage=False`` (``protocol.verifier_coverage: offline``)
records no coverage pass. ``produce_coverage_path`` makes it into the
Assessment store, never the job, with the same parity verdict and the same G
input the in-trial pass writes for the same snapshot; ``check_coverage_path``
makes it again and compares it with the trial's own pass or the stored run,
writing nothing. ``raidar assess --rescore`` makes it first only when a scorer
that reads it (``code-quality@4``) runs; the trial config defers it only when
the protocol says so.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.grading import offline, records
from raidar.grading.coverage import offline as coverage_module
from raidar.grading.coverage.offline import check_coverage_path, produce_coverage_path
from raidar.grading.coverage.store import read_offline_coverage
from raidar.grading.records import (
    FORK_EVIDENCE_FILE,
    coverage_identity,
    load_step_record,
    step_record_dir,
)
from raidar.grading.regrade import RegradeOptions
from raidar.grading.sandbox import GradingSandbox
from tests.scorer_support import tree_state
from tests.test_verifier_and_regrade import (
    FakeAgentEnvironment,
    make_verifier,
    place_step_artifact,
)
from tests.verifier_support import LocalProcessSandbox, copy_grading_sources, make_bundle

STEP = "checkpoint_2"


async def _graded(root: Path, *, coverage: bool, note: str | None = None) -> Path:
    """A trial whose checkpoint_2 was graded with or without the in-trial coverage pass."""
    bundle = make_bundle(root / "fixture")
    app = place_step_artifact(root, STEP, mul=True)
    if note is not None:
        (app / "NOTE.txt").write_text(note)  # a snapshot of its own (its own digest)
    verifier = await make_verifier(
        root, FakeAgentEnvironment(), step=STEP, bundle_root=bundle.root, coverage=coverage
    )
    await verifier.verify()
    return root / "trial"


def _options(root: Path) -> RegradeOptions:
    """The trial's own sandbox layout: the local sandbox's paths are its invocation identity
    (a Docker grader's are fixed container paths), so parity needs the same root."""

    async def build(bundle: Any) -> GradingSandbox:
        del bundle
        return LocalProcessSandbox(root / "sandbox")

    return RegradeOptions(docker_context="test-ctx", sandbox_factory=build)


async def test_a_deferred_coverage_pass_is_made_into_the_store_never_the_job(
    tmp_path: Path,
) -> None:
    trial = await _graded(tmp_path, coverage=False)
    record = load_step_record(step_record_dir(trial, STEP))
    assert not record.coverage.enabled
    assert not (step_record_dir(trial, STEP) / FORK_EVIDENCE_FILE).exists()
    store = tmp_path / "store"
    before = tree_state(trial)

    report = await produce_coverage_path(trial, store, _options(tmp_path))

    assert tree_state(trial) == before, "the job is never written"
    (entry,) = report["steps"]
    assert (entry["status"], entry["eligible"], report["errors"]) == ("produced", True, 0)
    stored = read_offline_coverage(
        store,
        record.bundle.digest,
        STEP,
        record.snapshot.digest,
        identity=coverage_identity(record, step_record_dir(trial, STEP)),
    )
    assert stored is not None
    assert stored.parity.eligible
    assert stored.fork is not None
    assert set(stored.fork) == {"transition_step", "baseline", "produced"}
    again = await produce_coverage_path(trial, store, _options(tmp_path))
    assert again["steps"][0]["status"] == "stored"
    check = await check_coverage_path(trial, store, _options(tmp_path))
    assert (check["match"], check["steps"][0]["against"]) == (True, "store")


async def test_shared_coverage_is_rechecked_against_each_trials_grading_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trial = await _graded(tmp_path, coverage=False)
    other = tmp_path / "other-trial"
    shutil.copytree(trial, other)
    evidence_path = step_record_dir(other, STEP) / "evidence.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["grading"]["cases"][0]["status"] = "failed"
    failed_case = evidence["grading"]["cases"][0]["id"]
    for entry in evidence["grading"]["evidence"]["ledger"]:
        if entry["node_id"] == failed_case and entry["phase"] == "call":
            entry["outcome"] = "failed"
    evidence_path.write_text(json.dumps(evidence))
    record_path = step_record_dir(other, STEP) / "record.json"
    record = json.loads(record_path.read_text())
    record["grade"]["cases"][0]["status"] = "failed"
    record_path.write_text(json.dumps(record))
    calls = 0
    original = coverage_module.run_oracle

    async def measured(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(coverage_module, "run_oracle", measured)
    store = tmp_path / "store"
    first = await produce_coverage_path(trial, store, _options(tmp_path))
    second = await produce_coverage_path(other, store, _options(tmp_path))
    assert first["steps"][0]["eligible"] is True
    assert second["steps"][0]["eligible"] is False
    assert first["steps"][0]["path"] != second["steps"][0]["path"]
    assert calls == 1, "the reusable coverage run is shared, never the parity decision"


@pytest.mark.parametrize("module", ["submission_deps.py", "sandbox.py", "grader.py"])
async def test_host_execution_changes_invalidate_reusable_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: str
) -> None:
    trial = await _graded(tmp_path, coverage=False)
    copied = copy_grading_sources(tmp_path / "implementation")
    monkeypatch.setattr(records, "__file__", str(copied))
    calls = 0
    original = coverage_module.run_oracle

    async def measured(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(coverage_module, "run_oracle", measured)
    store = tmp_path / "store"
    first = await produce_coverage_path(trial, store, _options(tmp_path))
    source = copied.parent / module
    source.write_text(source.read_text() + "\n# A comment leaves execution semantics alone.\n")
    unchanged = await produce_coverage_path(trial, store, _options(tmp_path))
    assert unchanged["steps"][0]["status"] == "stored"
    assert calls == 1
    source.write_text(source.read_text() + "\nHOST_EXECUTION_REVISION = 2\n")
    changed = await produce_coverage_path(trial, store, _options(tmp_path))
    assert changed["steps"][0]["status"] == "produced"
    assert changed["steps"][0]["path"] != first["steps"][0]["path"]
    assert calls == 2, "changed host execution must rerun coverage, not just recompute parity"


async def test_coverage_cache_separates_grader_timeouts_and_refuses_a_different_image(
    tmp_path: Path,
) -> None:
    trial = await _graded(tmp_path, coverage=False)
    store = tmp_path / "store"
    first = await produce_coverage_path(trial, store, _options(tmp_path))
    record_path = step_record_dir(trial, STEP) / "record.json"
    record = json.loads(record_path.read_text())
    record["grader"]["timeout_s"] += 1
    record_path.write_text(json.dumps(record))
    second = await produce_coverage_path(trial, store, _options(tmp_path))
    assert second["steps"][0]["status"] == "produced"
    assert first["steps"][0]["path"] != second["steps"][0]["path"]
    record["grader"]["environment_id"] = "different-image"
    record_path.write_text(json.dumps(record))
    third = await produce_coverage_path(trial, store, _options(tmp_path))
    assert third["errors"] == 1
    assert "environment differs" in third["steps"][0]["error"]


async def test_the_offline_run_reproduces_the_trials_own_coverage_pass(tmp_path: Path) -> None:
    trial = await _graded(tmp_path, coverage=True)
    record = load_step_record(step_record_dir(trial, STEP))
    assert record.coverage.enabled
    assert record.coverage.parity is not None
    assert record.coverage.parity.eligible
    before = tree_state(trial)

    report = await check_coverage_path(trial, None, _options(tmp_path))

    assert tree_state(trial) == before
    (entry,) = report["steps"]
    # Same parity verdict, same differences and a byte-equal G input (g-kernel-input.json).
    assert (report["match"], entry["status"], entry["against"]) == (True, "match", "trial")
    assert entry["differences"] == []


async def test_the_check_reports_a_g_input_that_differs(tmp_path: Path) -> None:
    trial = await _graded(tmp_path, coverage=True)
    fork_file = step_record_dir(trial, STEP) / FORK_EVIDENCE_FILE
    fork = json.loads(fork_file.read_text())
    fork["produced"]["coverage"] = []
    fork_file.write_text(json.dumps(fork))

    report = await check_coverage_path(trial, None, _options(tmp_path))

    assert report["match"] is False
    assert report["steps"][0]["differences"] == [f"{FORK_EVIDENCE_FILE}: differs in produced"]


async def test_a_deferred_step_with_nothing_stored_is_not_produced(tmp_path: Path) -> None:
    trial = await _graded(tmp_path, coverage=False)

    report = await check_coverage_path(trial, tmp_path / "empty", _options(tmp_path))

    assert (report["match"], report["steps"][0]["status"]) == (False, "not_produced")


async def test_a_changed_snapshot_is_an_error_never_a_run(tmp_path: Path) -> None:
    trial = await _graded(tmp_path, coverage=False)
    (trial / "steps" / STEP / "artifacts" / "app" / "app.py").write_text("print('edited')\n")

    report = await produce_coverage_path(trial, tmp_path / "store", _options(tmp_path))

    assert report["errors"] == 1
    assert "changed since grading" in report["steps"][0]["error"]
    assert not (tmp_path / "store").exists()


@pytest.mark.parametrize(
    ("scorers", "replay", "makes"),
    [
        (("code-quality@4",), False, True),
        (("code-quality@4", "correctness@3"), True, False),
        (("correctness@3", "resource-usage@2"), False, False),
    ],
)
def test_rescore_makes_coverage_first_only_for_a_scorer_that_reads_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scorers: tuple[str, ...],
    replay: bool,
    makes: bool,
) -> None:
    called: list[Any] = []

    async def fake(
        path: Path, store: Path, options: RegradeOptions, concurrency: int = 1
    ) -> dict[str, Any]:
        called.append((path, store, options.docker_context, concurrency))
        return {"steps": [], "errors": 0}

    monkeypatch.setattr(offline, "produce_coverage_path", fake)

    result = offline.produce_coverage(
        tmp_path / "job",
        tmp_path / "store",
        scorers,
        RegradeOptions(docker_context="orbstack"),
        replay=replay,
        concurrency=2,
    )

    assert (result is not None) is makes
    assert called == ([(tmp_path / "job", tmp_path / "store", "orbstack", 2)] if makes else [])


def test_coverage_check_needs_regrade(tmp_path: Path) -> None:
    done = CliRunner().invoke(
        main, ["assess", "--rescore", str(tmp_path), "--coverage-check", "--store", "s"]
    )

    assert done.exit_code == 2
    assert "--coverage-check applies to --regrade only" in done.output


async def test_trials_made_concurrently_give_the_report_made_one_after_another(
    tmp_path: Path,
) -> None:
    """``--concurrency``: trials side by side, each one's steps in order, entries in trial order."""
    job = tmp_path / "job"
    job.mkdir()
    for name in ("t1", "t2", "t3"):
        (job / name).symlink_to(await _graded(tmp_path / name, coverage=False, note=name))

    async def build(bundle: Any) -> GradingSandbox:
        # Each trial was graded in its own root's sandbox (the bundle sits at <root>/fixture).
        return LocalProcessSandbox(Path(bundle.root).parent.parent / "sandbox")

    options = RegradeOptions(docker_context="test-ctx", sandbox_factory=build)
    one = await produce_coverage_path(job, tmp_path / "one", options)
    many = await produce_coverage_path(job, tmp_path / "many", options, concurrency=3)

    def shape(report: dict[str, Any]) -> list[tuple[Any, ...]]:
        return [
            (Path(e["trial_dir"]).name, e["step"], e["status"], e["eligible"])
            for e in report["steps"]
        ]

    assert shape(one) == shape(many) == [(f"t{i}", STEP, "produced", True) for i in (1, 2, 3)]
    assert (one["errors"], many["errors"]) == (0, 0)
    for entry in many["steps"]:
        stored = json.loads(Path(entry["path"]).read_text())
        twin = json.loads(
            (tmp_path / "one" / Path(entry["path"]).relative_to(tmp_path / "many")).read_text()
        )
        assert {k: v for k, v in stored.items() if k not in ("produced_at", "coverage_run_s")} == {
            k: v for k, v in twin.items() if k not in ("produced_at", "coverage_run_s")
        }
    checked = await check_coverage_path(job, tmp_path / "many", options, concurrency=2)
    assert checked["match"], checked
