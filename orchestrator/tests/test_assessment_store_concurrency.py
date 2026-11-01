"""Assessment store seams: concurrent writers, stored trials and the rescore CLI."""

from __future__ import annotations

import json
import multiprocessing
import re
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from raidar.cli import main
from raidar.contract.references import ResolverSet
from raidar.design.experiment.loader import load_experiment
from raidar.design.experiment.lock import build_lock, render_lock
from raidar.grading.models import CaseStatus
from raidar.grading.records import EVIDENCE_FILE, RECORD_FILE, step_record_dir
from raidar.scoring.catalog.builtin import BUILTIN_IMPLEMENTATIONS, scorer_resolver
from raidar.scoring.framework.metrics import MetricResolver
from raidar.scoring.framework.rescore import RescoreError, RescoreOutcome, rescore_path
from raidar.scoring.framework.result import AssessResult, AssessStatus
from raidar.scoring.framework.store import (
    Assessment,
    AssessmentConflictError,
    AssessmentStore,
    ScorerIdentity,
)
from raidar.scoring.framework.trial_inputs import TrialInputError, load_trial_context
from tests.experiment_spec_support import (
    MECHANICS_SMOKE,
    SMOKE_EXPERIMENT,
    experiment_document,
    fixture_resolvers,
    write_experiment,
)
from tests.scorer_support import (
    APP_V1,
    APP_V2,
    StepFixture,
    fake_registry,
    tree_state,
    two_step_trial,
    write_toy_bundle,
    write_trial,
)
from tests.verifier_support import copy_grading_sources

pytestmark = pytest.mark.slow


def _promote(root: Path, value: int, writer: int, start: Path) -> None:
    store = AssessmentStore(root)
    provisional = store.get("a" * 64)
    assert provisional is not None
    complete = provisional.model_copy(
        update={
            "provisional": False,
            "result": AssessResult(
                status=AssessStatus.UNAVAILABLE, reason=str(value), observations=()
            ),
        }
    )
    original = AssessmentStore._supersede

    def delayed(self: AssessmentStore, path: Path, stored: bytes, data: bytes, key: str) -> None:
        # Enlarge the real read/replace race; the store must serialize this window.
        time.sleep(0.2)
        original(self, path, stored, data, key)

    (root / f"ready-{writer}").touch()
    deadline = time.monotonic() + 5
    while not start.exists():
        if time.monotonic() > deadline:
            raise TimeoutError("writers were not started")
        time.sleep(0.01)
    with patch.object(AssessmentStore, "_supersede", delayed):
        try:
            outcome = store.put(complete).value
        except AssessmentConflictError:
            outcome = "conflict"
    (root / f"result-{writer}.json").write_text(json.dumps({"outcome": outcome, "value": value}))


@pytest.mark.parametrize("values", [(1, 1), (1, 2)])
def test_competing_promotions_preserve_one_complete_result_and_detect_divergence(
    tmp_path: Path, values: tuple[int, int]
) -> None:
    store = AssessmentStore(tmp_path / "store")
    provisional = Assessment(
        key="a" * 64,
        scorer=ScorerIdentity(ref="test@1", manifest_digest="test", implementation_digest=None),
        config={},
        tool_lock={},
        inputs={},
        result=AssessResult(status=AssessStatus.UNAVAILABLE, reason="awaiting", observations=()),
        provisional=True,
    )
    store.put(provisional)
    start = store.root / "start"
    context = multiprocessing.get_context("spawn")
    writers = [
        context.Process(target=_promote, args=(store.root, value, writer, start))
        for writer, value in enumerate(values)
    ]
    try:
        for writer in writers:
            writer.start()
        deadline = time.monotonic() + 10
        while not all((store.root / f"ready-{writer}").exists() for writer in range(2)):
            assert time.monotonic() < deadline, "writers failed to become ready"
            time.sleep(0.01)
        start.touch()
        for writer in writers:
            writer.join(timeout=10)
            assert writer.exitcode == 0
    finally:
        for writer in writers:
            if writer.is_alive():
                writer.terminate()
                writer.join(timeout=5)
    results = [
        json.loads((store.root / f"result-{writer}.json").read_text()) for writer in range(2)
    ]
    expected = ["identical", "superseded"] if values[0] == values[1] else ["conflict", "superseded"]
    assert sorted(result["outcome"] for result in results) == expected
    winner = next(result for result in results if result["outcome"] == "superseded")
    complete = store.get(provisional.key)
    assert complete is not None
    assert not complete.provisional
    assert complete.result.reason == str(winner["value"])
    assert (
        store.superseded_path(provisional.key, provisional.render()).read_bytes()
        == provisional.render()
    )


# ---- stored-trial and CLI seams: the decisions are proved fast over in-memory inputs ---------

V1 = "fake-quality@1"
V2 = "fake-quality@2"


@pytest.mark.parametrize("change", ["evidence_changed", "evidence_removed", "image", "timeout"])
def test_completed_assessments_are_rekeyed_when_grading_evidence_or_runtime_changes(
    tmp_path: Path, change: str
) -> None:
    store = AssessmentStore(tmp_path / "store")
    trial = two_step_trial(tmp_path / "trial")
    record_dir = step_record_dir(trial, "checkpoint_1")
    evidence = record_dir / EVIDENCE_FILE
    evidence.write_text('{"grading": "original"}')
    (original,) = rescore_path(trial, [V1], store, fake_registry())
    original_bytes = original.path.read_bytes()
    if change == "evidence_changed":
        evidence.write_text('{"grading": "different"}')
    elif change == "evidence_removed":
        evidence.unlink()
    else:
        path = record_dir / RECORD_FILE
        record = json.loads(path.read_text())
        field = "environment_id" if change == "image" else "timeout_s"
        record["grader"][field] = "sha256:different-image" if change == "image" else 123.0
        path.write_text(json.dumps(record))
    (updated,) = rescore_path(trial, [V1], store, fake_registry())
    assert updated.key != original.key
    assert updated.outcome is RescoreOutcome.ADDED
    assert original.path.read_bytes() == original_bytes


def test_completed_assessments_are_rekeyed_when_offline_grading_plugin_bytes_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied = copy_grading_sources(tmp_path / "implementation")
    plugin = copied.parent / "runtime" / "raidar_oracle_plugin.py"
    monkeypatch.setattr("raidar.grading.records.__file__", str(copied))
    store = AssessmentStore(tmp_path / "store")
    trial = two_step_trial(tmp_path / "trial")
    (original,) = rescore_path(trial, [V1], store, fake_registry())
    plugin.write_bytes(plugin.read_bytes() + b"\n# Changed sandbox plugin payload.\n")
    (updated,) = rescore_path(trial, [V1], store, fake_registry())
    assert updated.key != original.key
    assert updated.outcome is RescoreOutcome.ADDED


def test_a_job_directory_is_rescored_without_mutating_the_runs(tmp_path: Path) -> None:
    job = tmp_path / "job"
    two_step_trial(job / "trial-a")
    write_trial(
        job / "trial-b",
        [
            StepFixture("checkpoint_1", APP_V1, (CaseStatus.PASSED,)),
            StepFixture("checkpoint_2", APP_V2, (CaseStatus.PASSED,)),
        ],
    )
    before = tree_state(job)
    store = AssessmentStore(tmp_path / "store")
    records = rescore_path(job, [V1], store, fake_registry())
    assert [record.trial for record in records] == ["trial-a", "trial-b"]
    assert tree_state(job) == before


def test_trials_that_stop_early_follow_the_bundle_plan_they_were_graded_with(
    tmp_path: Path,
) -> None:
    halted = write_trial(
        tmp_path / "halted",
        [StepFixture("checkpoint_1", APP_V1, (CaseStatus.FAILED,))],
        planned=["checkpoint_2", "checkpoint_3"],
    )
    store = AssessmentStore(tmp_path / "store")
    (record,) = rescore_path(halted, [V1], store, fake_registry())
    stored = store.get(record.key)
    assert stored is not None
    assert [s["reached"] for s in stored.inputs["steps"]] == [True, False, False]

    plan = write_toy_bundle(tmp_path / "plan", ["checkpoint_1", "checkpoint_2"])
    with pytest.raises(RescoreError, match="does not match the digest"):
        rescore_path(halted, [V1], store, fake_registry(), bundle_override=plan.root)
    swapped = write_trial(tmp_path / "swapped", [StepFixture("checkpoint_2", APP_V1, None)])
    with pytest.raises(TrialInputError, match="not a prefix of the planned checkpoints"):
        load_trial_context(swapped, bundle_override=plan.root)


def test_a_snapshot_changed_after_grading_cannot_be_assessed(tmp_path: Path) -> None:
    trial = two_step_trial(tmp_path / "trial")
    (trial / "steps/checkpoint_1/artifacts/app/app/main.py").write_text("tampered\n")
    with pytest.raises(RescoreError, match="changed since grading"):
        rescore_path(trial, [V1], AssessmentStore(tmp_path / "store"), fake_registry())


def test_assess_rescore_cli_reports_added_existing_and_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("raidar.cli.assess.default_scorer_registry", fake_registry)
    trial = two_step_trial(tmp_path / "trial")
    store = tmp_path / "store"
    runner = CliRunner()
    args = ["assess", "--rescore", str(trial), "--scorer", V1, "--store", str(store)]

    added = runner.invoke(main, args)
    assert added.exit_code == 0, added.output
    assert json.loads(added.output)["counts"]["added"] == 1
    existing = runner.invoke(main, args)
    assert json.loads(existing.output)["counts"]["existing"] == 1
    replayed = runner.invoke(main, [*args, "--replay"])
    assert json.loads(replayed.output)["counts"]["replayed_identical"] == 1

    inside = runner.invoke(
        main, ["assess", "--rescore", str(trial), "--scorer", V1, "--store", str(trial / "s")]
    )
    assert inside.exit_code == 2
    assert "must not be inside" in inside.output
    no_store = runner.invoke(main, ["assess", "--rescore", str(trial), "--scorer", V1])
    assert no_store.exit_code == 2
    both = runner.invoke(main, ["assess", "--rescore", str(trial), "--regrade", str(trial)])
    assert both.exit_code == 2


def test_assess_rescore_cli_config_sets_the_inventory_policy_and_scorer_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("raidar.cli.assess.default_scorer_registry", fake_registry)
    trial = two_step_trial(tmp_path / "trial")
    store = AssessmentStore(tmp_path / "store")
    config = tmp_path / "assess.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "inventory_policy": {"generated_globs": ["app/**"]},
                "scorers": {V1: {"threshold": 3}},
            }
        )
    )
    base = ["assess", "--rescore", str(trial), "--scorer", V1, "--store", str(store.root)]
    runner = CliRunner()
    plain = runner.invoke(main, base)
    configured = runner.invoke(main, [*base, "--config", str(config)])
    assert configured.exit_code == 0, configured.output
    keys = [json.loads(out.output)["assessments"][0]["key"] for out in (plain, configured)]
    assert keys[0] != keys[1]
    assessment = store.get(keys[1])
    assert assessment is not None
    assert assessment.config == {"threshold": 3}
    assert assessment.inputs["inventory_policy"]["generated_globs"] == ["app/**"]
    production = {
        o.subject: o.value for o in assessment.result.observations if o.metric == "production_files"
    }
    assert production == {"checkpoint_1": 0, "checkpoint_2": 0}

    config.write_text(yaml.safe_dump({"scorers": {V2: {}}}))
    unused = runner.invoke(main, [*base, "--config", str(config)])
    assert unused.exit_code == 1
    assert "not being run" in unused.output
    config.write_text(yaml.safe_dump({"inventory_policy": {"globs": ["x"]}}))
    invalid = runner.invoke(main, [*base, "--config", str(config)])
    assert invalid.exit_code == 1
    assert "invalid rescore config" in invalid.output


def test_assess_rescore_cli_refuses_scorers_that_cannot_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(BUILTIN_IMPLEMENTATIONS, "correctness@10")
    trial = two_step_trial(tmp_path / "trial")
    runner = CliRunner()
    store = str(tmp_path / "store")
    unimplemented = runner.invoke(
        main, ["assess", "--rescore", str(trial), "--scorer", "correctness@10", "--store", store]
    )
    assert unimplemented.exit_code == 1
    assert "no implementation" in unimplemented.output
    unknown = runner.invoke(
        main, ["assess", "--rescore", str(trial), "--scorer", "nope@1", "--store", store]
    )
    assert unknown.exit_code == 1
    assert "unknown scorer" in unknown.output
    assert not (tmp_path / "store").exists()


def test_assess_rescore_cli_reports_malformed_trials_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("raidar.cli.assess.default_scorer_registry", fake_registry)
    runner = CliRunner()
    for index, (corrupt, message) in enumerate(
        (
            (
                lambda r: r["step_results"][0].pop("step_name"),
                r"step_results\[0\] has no step_name",
            ),
            (lambda r: r.update(step_results="checkpoint_1"), "step_results is not a list"),
        )
    ):
        trial = two_step_trial(tmp_path / f"trial-{index}")
        result = json.loads((trial / "result.json").read_text())
        corrupt(result)
        (trial / "result.json").write_text(json.dumps(result))
        args = ["assess", "--rescore", str(trial), "--scorer", V1, "--store", str(tmp_path / "s")]
        outcome = runner.invoke(main, args)
        assert outcome.exit_code == 1
        assert not isinstance(outcome.exception, KeyError)
        assert re.search(message, outcome.output), outcome.output
    (trial / "result.json").write_text("{not json")
    broken = runner.invoke(main, args)
    assert broken.exit_code == 1
    assert "not valid JSON" in broken.output


def test_lock_records_scorer_manifests_and_tool_pins_byte_identically(tmp_path: Path) -> None:
    scorers = _committed_scorer_refs()
    path = write_experiment(tmp_path, experiment_document(assessment={"scorers": scorers}))
    problem, agent, workflow, _ = fixture_resolvers()
    resolvers = ResolverSet(
        [problem, agent, workflow, MetricResolver(), scorer_resolver()], entry_point_group=None
    )
    lock_path = path.with_name("experiment.lock.json")
    first = render_lock(build_lock(load_experiment(path), lock_path=lock_path, resolvers=resolvers))
    second = render_lock(
        build_lock(load_experiment(path), lock_path=lock_path, resolvers=resolvers)
    )
    assert first == second
    lock = json.loads(first)
    assert sorted(lock["assessment"]["references"]["scorer"]) == scorers
    assert lock["assessment"]["tools"]["ruff"] == "0.8.6"
    # Scorer digests and pinned tools are assessment inputs, never run identity.
    assert "ruff" not in lock["execution"]["tools"]


def _committed_scorer_refs() -> list[str]:
    refs: list[str] = []
    for path in (MECHANICS_SMOKE, SMOKE_EXPERIMENT):
        refs += yaml.safe_load(path.read_text())["assessment"]["scorers"]
    return sorted(set(refs))
