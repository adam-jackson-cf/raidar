"""Assessment store and `raidar assess --rescore` (plan F2)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from raidar.cli import main
from raidar.grading.models import CaseStatus
from raidar.scoring.framework.catalog import BUILTIN_IMPLEMENTATIONS
from raidar.scoring.framework.rescore import RescoreError, RescoreOutcome, rescore_path
from raidar.scoring.framework.store import AssessmentConflictError, AssessmentStore
from raidar.scoring.framework.trial_inputs import TrialInputError, load_trial_context
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

V1 = "fake-quality@1"
V2 = "fake-quality@2"


def _job(tmp_path: Path) -> Path:
    """Two trials whose second checkpoints graded differently."""
    job = tmp_path / "job"
    two_step_trial(job / "trial-a")
    write_trial(
        job / "trial-b",
        [
            StepFixture("checkpoint_1", APP_V1, (CaseStatus.PASSED,)),
            StepFixture("checkpoint_2", APP_V2, (CaseStatus.PASSED,)),
        ],
    )
    return job


def _objects(store: AssessmentStore) -> dict[str, bytes]:
    return {key: store.path_for(key).read_bytes() for key in store.stored_keys()}


def test_rescoring_with_a_new_scorer_version_adds_assessments_and_never_mutates_runs(
    tmp_path: Path,
) -> None:
    job = _job(tmp_path)
    before = tree_state(job)
    store = AssessmentStore(tmp_path / "store")

    first = rescore_path(job, [V1], store, fake_registry())
    assert [record.outcome for record in first] == [RescoreOutcome.ADDED] * 2
    v1_objects = _objects(store)

    second = rescore_path(job, [V2], store, fake_registry())
    assert [record.outcome for record in second] == [RescoreOutcome.ADDED] * 2
    assert {record.key for record in second}.isdisjoint(record.key for record in first)
    after = _objects(store)
    assert len(after) == 4
    assert {key: after[key] for key in v1_objects} == v1_objects

    again = [
        *rescore_path(job, [V1], store, fake_registry()),
        *rescore_path(job, [V2], store, fake_registry()),
    ]
    assert {record.outcome for record in again} == {RescoreOutcome.EXISTING}
    assert _objects(store) == after
    assert tree_state(job) == before

    stored = store.get(second[0].key)
    assert stored is not None
    assert stored.scorer.ref == V2
    assert {o.metric for o in stored.result.observations} >= {"test_files"}


def test_deterministic_replay_is_byte_identical_and_a_divergent_replay_is_refused(
    tmp_path: Path,
) -> None:
    job = _job(tmp_path)
    store = AssessmentStore(tmp_path / "store")
    added = rescore_path(job, [V1], store, fake_registry())
    original = _objects(store)

    replayed = rescore_path(job, [V1], store, fake_registry(), replay=True)
    assert {record.outcome for record in replayed} == {RescoreOutcome.REPLAYED_IDENTICAL}
    assert _objects(store) == original

    elsewhere = AssessmentStore(tmp_path / "other-store")
    rescore_path(job, [V1], elsewhere, fake_registry())
    assert _objects(elsewhere) == original

    assessment = store.get(added[0].key)
    assert assessment is not None
    tampered = assessment.model_copy(
        update={"result": assessment.result.model_copy(update={"observations": ()})}
    )
    with pytest.raises(AssessmentConflictError, match="different bytes"):
        store.put(tampered)
    assert _objects(store) == original
    outcomes = [entry["outcome"] for entry in store.read_log()]
    assert outcomes == ["added", "added", "replayed_identical", "replayed_identical"]


def test_assessment_keys_follow_config_and_inputs(tmp_path: Path) -> None:
    store = AssessmentStore(tmp_path / "store")
    trial = two_step_trial(tmp_path / "jobs" / "trial")
    plain = rescore_path(trial, [V1], store, fake_registry())
    configured = rescore_path(trial, [V1], store, fake_registry(), config={V1: {"threshold": 3}})
    copy = two_step_trial(tmp_path / "elsewhere" / "trial-copy")
    same_inputs = rescore_path(copy, [V1], store, fake_registry())
    assert plain[0].key != configured[0].key
    # Identical content in two trials is one Assessment: the key is content, not location.
    assert same_inputs[0].key == plain[0].key
    assert same_inputs[0].outcome is RescoreOutcome.EXISTING


def test_scores_come_from_oracle_records_and_unavailable_steps_carry_no_value(
    tmp_path: Path,
) -> None:
    trial = write_trial(
        tmp_path / "trial",
        [
            StepFixture("checkpoint_1", APP_V1, (CaseStatus.PASSED,)),
            StepFixture("checkpoint_2", APP_V1, None),
        ],
    )
    store = AssessmentStore(tmp_path / "store")
    (record,) = rescore_path(trial, [V1], store, fake_registry())
    assessment = store.get(record.key)
    assert assessment is not None
    values = {(o.metric, o.subject): o for o in assessment.result.observations}
    # Harbor's aggregated rewards in result.json say solved=0.0; the verifier record says solved.
    assert values[("solved", "checkpoint_1")].value == 1.0
    assert values[("solved_checkpoints", "trajectory")].value == 1
    ungraded = values[("solved", "checkpoint_2")]
    assert ungraded.status.value == "unavailable"
    assert ungraded.value is None
    assert assessment.inputs["steps"][1]["oracle_record"] is None


def test_trials_that_stop_early_keep_every_planned_checkpoint_as_a_subject(
    tmp_path: Path,
) -> None:
    halted = write_trial(
        tmp_path / "halted",
        [StepFixture("checkpoint_1", APP_V1, (CaseStatus.FAILED,))],
        planned=["checkpoint_2", "checkpoint_3"],
    )
    complete = write_trial(
        tmp_path / "complete",
        [
            StepFixture(name, APP_V1, (CaseStatus.PASSED,))
            for name in ("checkpoint_1", "checkpoint_2", "checkpoint_3")
        ],
    )
    store = AssessmentStore(tmp_path / "store")
    subjects = []
    for trial in (halted, complete):
        (record,) = rescore_path(trial, [V1], store, fake_registry())
        assessment = store.get(record.key)
        assert assessment is not None
        subjects.append([(o.metric, o.subject) for o in assessment.result.observations])
    assert subjects[0] == subjects[1]
    halted_result = store.get(rescore_path(halted, [V1], store, fake_registry())[0].key)
    assert halted_result is not None
    by_key = {(o.metric, o.subject): o for o in halted_result.result.observations}
    unreached = by_key[("production_files", "checkpoint_3")]
    assert (unreached.status.value, unreached.value, unreached.reason) == (
        "not_applicable",
        None,
        "checkpoint not reached",
    )
    assert by_key[("growth", "checkpoint_1->checkpoint_2")].value is None
    assert [s["reached"] for s in halted_result.inputs["steps"]] == [True, False, False]

    # The plan comes from the bundle the records were graded with, and it must be followed.
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
    monkeypatch.delitem(BUILTIN_IMPLEMENTATIONS, "correctness@1")
    trial = two_step_trial(tmp_path / "trial")
    runner = CliRunner()
    store = str(tmp_path / "store")
    unimplemented = runner.invoke(
        main, ["assess", "--rescore", str(trial), "--scorer", "correctness@1", "--store", store]
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
