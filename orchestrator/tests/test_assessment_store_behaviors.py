"""Assessment store behaviour over in-memory trial inputs (plan F2)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raidar.contract.job_layout import TRIAL_RECORDS_DIR
from raidar.contract.snapshot import tree_digest
from raidar.grading.bundle import BundleError
from raidar.grading.models import CaseStatus, VerifierGrade
from raidar.grading.records import VERIFIER_RECORD_DIR, StepRecord, step_record_dir
from raidar.scoring.framework.context import AssessContext
from raidar.scoring.framework.rescore import (
    RescoreError,
    RescoreOutcome,
    _loaded_trials,
    assess_trials,
    report_payload,
    rescore_path,
)
from raidar.scoring.framework.store import AssessmentConflictError, AssessmentStore
from raidar.scoring.framework.trial_inputs import (
    BundleCache,
    BundleUnavailableError,
    TrialInputError,
    _bundle_root,
    _check_problems,
    _checked_snapshot,
    _configured_bundle,
    _Executed,
    _executed_from,
    _load_records,
    _oracle_bundle_of,
    _plan,
    _planned_steps,
    _record_digest,
    _recorded_steps,
    _step_inputs,
    _task_plan,
    _trial_record_steps,
    _unreached,
    load_trial_context,
    problem_config,
)
from raidar.scoring.framework.trial_inputs import _bundle as _bundle_of
from tests.scorer_support import (
    APP_V1,
    APP_V2,
    StepFixture,
    assess_fixed,
    fake_registry,
    fixed_identity,
    memory_record,
    memory_step,
    snapshot_context,
    tree_state,
    two_step_context,
)

V1 = "fake-quality@1"
V2 = "fake-quality@2"
P, F = CaseStatus.PASSED, CaseStatus.FAILED


def _trials(root: Path) -> dict[str, AssessContext]:
    """Two trials whose second checkpoints graded differently."""
    return {
        "trial-a": two_step_context(root / "trial-a"),
        "trial-b": snapshot_context(
            root / "trial-b",
            [StepFixture("checkpoint_1", APP_V1, (P,)), StepFixture("checkpoint_2", APP_V2, (P,))],
        ),
    }


def _objects(store: AssessmentStore) -> dict[str, bytes]:
    return {key: store.path_for(key).read_bytes() for key in store.stored_keys()}


def _run(store: AssessmentStore, ref: str, trials: dict[str, AssessContext], **kwargs):  # type: ignore[no-untyped-def]
    registration = fake_registry().get(ref)
    return [
        assess_fixed(store, registration, ctx, trial=name, **kwargs) for name, ctx in trials.items()
    ]


def test_rescoring_with_a_new_scorer_version_adds_assessments_and_never_mutates_inputs(
    tmp_path: Path,
) -> None:
    trials = _trials(tmp_path / "runs")
    before = tree_state(tmp_path / "runs")
    store = AssessmentStore(tmp_path / "store")

    first = _run(store, V1, trials)
    assert [record.outcome for record in first] == [RescoreOutcome.ADDED] * 2
    v1_objects = _objects(store)

    second = _run(store, V2, trials)
    assert [record.outcome for record in second] == [RescoreOutcome.ADDED] * 2
    assert {record.key for record in second}.isdisjoint(record.key for record in first)
    after = _objects(store)
    assert len(after) == 4
    assert {key: after[key] for key in v1_objects} == v1_objects

    again = [*_run(store, V1, trials), *_run(store, V2, trials)]
    assert {record.outcome for record in again} == {RescoreOutcome.EXISTING}
    assert _objects(store) == after
    assert tree_state(tmp_path / "runs") == before

    stored = store.get(second[0].key)
    assert stored is not None
    assert stored.scorer.ref == V2
    assert {o.metric for o in stored.result.observations} >= {"test_files"}


def test_deterministic_replay_is_byte_identical_and_a_divergent_replay_is_refused(
    tmp_path: Path,
) -> None:
    trials = _trials(tmp_path / "runs")
    store = AssessmentStore(tmp_path / "store")
    added = _run(store, V1, trials)
    original = _objects(store)

    replayed = _run(store, V1, trials, replay=True)
    assert {record.outcome for record in replayed} == {RescoreOutcome.REPLAYED_IDENTICAL}
    assert _objects(store) == original

    elsewhere = AssessmentStore(tmp_path / "other-store")
    _run(elsewhere, V1, trials)
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
    ctx = two_step_context(tmp_path / "one")
    registration = fake_registry().get(V1)
    plain = assess_fixed(store, registration, ctx, trial="t")
    configured = assess_fixed(store, registration, replace(ctx, config={"threshold": 3}), trial="t")
    copy = assess_fixed(store, registration, two_step_context(tmp_path / "elsewhere"), trial="copy")
    assert plain.key != configured.key
    # Identical content in two places is one Assessment: the key is content, not location.
    assert copy.key == plain.key
    assert copy.outcome is RescoreOutcome.EXISTING


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("record_digest", "other-grading-evidence"),
        ("record_digest", None),
        ("snapshot_digest", "other-snapshot"),
        ("trace_digest", "other-trace"),
    ],
)
def test_completed_assessments_are_rekeyed_when_any_run_input_changes(
    tmp_path: Path, field: str, changed: str | None
) -> None:
    store = AssessmentStore(tmp_path / "store")
    ctx = two_step_context(tmp_path / "trial")
    registration = fake_registry().get(V1)
    original = assess_fixed(store, registration, ctx, trial="t")
    original_bytes = original.path.read_bytes()
    first = replace(ctx.steps[0], **{field: changed})
    updated = assess_fixed(
        store, registration, replace(ctx, steps=(first, *ctx.steps[1:])), trial="t"
    )
    assert updated.key != original.key
    assert updated.outcome is RescoreOutcome.ADDED
    assert original.path.read_bytes() == original_bytes


def test_scores_come_from_oracle_records_and_unavailable_steps_carry_no_value(
    tmp_path: Path,
) -> None:
    ctx = snapshot_context(
        tmp_path / "trial",
        [StepFixture("checkpoint_1", APP_V1, (P,)), StepFixture("checkpoint_2", APP_V1, None)],
    )
    store = AssessmentStore(tmp_path / "store")
    record = assess_fixed(store, fake_registry().get(V1), ctx, trial="t")
    assessment = store.get(record.key)
    assert assessment is not None
    values = {(o.metric, o.subject): o for o in assessment.result.observations}
    assert values[("solved", "checkpoint_1")].value == 1.0
    assert values[("solved_checkpoints", "trajectory")].value == 1
    ungraded = values[("solved", "checkpoint_2")]
    assert ungraded.status.value == "unavailable"
    assert ungraded.value is None
    assert assessment.inputs["steps"][1]["oracle_record"] is None


def test_trials_that_stop_early_keep_every_planned_checkpoint_as_a_subject(
    tmp_path: Path,
) -> None:
    halted = snapshot_context(
        tmp_path / "halted",
        [StepFixture("checkpoint_1", APP_V1, (F,))],
        planned=["checkpoint_2", "checkpoint_3"],
    )
    complete = snapshot_context(
        tmp_path / "complete",
        [
            StepFixture(name, APP_V1, (P,))
            for name in ("checkpoint_1", "checkpoint_2", "checkpoint_3")
        ],
    )
    store = AssessmentStore(tmp_path / "store")
    registration = fake_registry().get(V1)
    stored = []
    for name, ctx in (("halted", halted), ("complete", complete)):
        assessment = store.get(assess_fixed(store, registration, ctx, trial=name).key)
        assert assessment is not None
        stored.append(assessment)
    assert [(o.metric, o.subject) for o in stored[0].result.observations] == [
        (o.metric, o.subject) for o in stored[1].result.observations
    ]
    by_key = {(o.metric, o.subject): o for o in stored[0].result.observations}
    unreached = by_key[("production_files", "checkpoint_3")]
    assert (unreached.status.value, unreached.value) == ("not_applicable", None)
    assert by_key[("growth", "checkpoint_1->checkpoint_2")].value is None
    assert [s["reached"] for s in stored[0].inputs["steps"]] == [True, False, False]


def test_a_job_assesses_every_trial_with_each_scorer_under_its_own_config(
    tmp_path: Path,
) -> None:
    trials = _trials(tmp_path / "runs")
    store = AssessmentStore(tmp_path / "store")
    registry = fake_registry()
    registrations = [registry.get(ref) for ref in (V1, V2)]
    config = {V1: {"threshold": 3}}

    records = assess_trials(
        store, registrations, trials.items(), config=config, identity_for=fixed_identity
    )

    assert [(r.trial, r.scorer) for r in records] == [
        ("trial-a", V1),
        ("trial-a", V2),
        ("trial-b", V1),
        ("trial-b", V2),
    ]
    configs = {r.scorer: store.get(r.key).config for r in records}  # type: ignore[union-attr]
    assert configs == {V1: {"threshold": 3}, V2: {}}
    payload = report_payload(records)
    counts = {"added": 4, "existing": 0, "replayed_identical": 0, "superseded": 0}
    assert payload["counts"] == counts
    assert [item["trial"] for item in payload["assessments"]][:2] == ["trial-a", "trial-a"]  # type: ignore[index]


def test_a_rescore_request_that_cannot_run_is_refused_before_any_trial_is_read(
    tmp_path: Path,
) -> None:
    store = AssessmentStore(tmp_path / "store")
    with pytest.raises(RescoreError, match="at least one scorer"):
        rescore_path(tmp_path, [], store, fake_registry())
    with pytest.raises(RescoreError, match="not being run"):
        rescore_path(tmp_path, [V1], store, fake_registry(), config={V2: {}})
    with pytest.raises(RescoreError, match="no .* records"):
        rescore_path(tmp_path, [V1], store, fake_registry())


@pytest.mark.parametrize("result", [None, "{not json", "[1]"])
def test_a_trial_whose_result_cannot_be_read_is_a_rescore_error_not_a_crash(
    tmp_path: Path, result: str | None
) -> None:
    trial = tmp_path / "trial"
    (trial / VERIFIER_RECORD_DIR).mkdir(parents=True)
    if result is not None:
        (trial / "result.json").write_text(result)
    with pytest.raises(RescoreError):
        rescore_path(trial, [V1], AssessmentStore(tmp_path / "store"), fake_registry())


def test_trials_are_loaded_one_at_a_time_and_recorded_under_their_review_id(
    tmp_path: Path,
) -> None:
    job = tmp_path / "pilot-job"
    trials = [job / "trials" / name for name in ("t1", "t2")]
    contexts = _trials(tmp_path / "runs")
    loaded: list[str] = []

    def load(trial: Path) -> AssessContext:
        loaded.append(trial.name)
        return contexts["trial-a"]

    stream = _loaded_trials(trials, load, lambda trial: f"pilot.run-a.{trial.name}")
    assert loaded == []
    first = next(stream)
    assert (first[0], loaded) == ("pilot.run-a.t1", ["t1"])
    assert first[1] is contexts["trial-a"]
    assert [trial_id for trial_id, _ in stream] == ["pilot.run-a.t2"]


def test_an_unloadable_trial_is_a_rescore_error(
    tmp_path: Path,
) -> None:
    trial = tmp_path / "job" / "trials" / "t1"

    def unloadable(_: Path) -> AssessContext:
        raise TrialInputError("result.json is missing")

    with pytest.raises(RescoreError, match="result.json is missing"):
        list(_loaded_trials([trial], unloadable))


# ---- loading a stored trial: the decisions, over parsed records and injected readers --------


def _oracle_records(*digests: str) -> dict[str, StepRecord]:
    records = {}
    for index, digest in enumerate(digests, 1):
        name = f"checkpoint_{index}"
        record = memory_record(name, _grade_of(name))
        bundle = record.bundle.model_copy(update={"digest": digest})
        records[name] = record.model_copy(update={"bundle": bundle})
    return records


def _grade_of(name: str) -> VerifierGrade:
    grade = memory_step(name, (P,)).grade
    assert grade is not None
    return grade


def _bundle(digest: str, problem: str = "toy") -> Any:
    return SimpleNamespace(digest=lambda: digest, manifest=SimpleNamespace(problem=problem))


TRIAL = Path("job/trials/t")


def test_executed_steps_are_the_harbor_step_results_or_the_one_oracle_record() -> None:
    result = {
        "step_results": [
            {"step_name": "checkpoint_1", "agent_result": {"n": 1}, "agent_execution": {"s": 2}},
            {"step_name": "checkpoint_2"},
        ]
    }
    first, second = _executed_from(TRIAL, result, [])
    assert (first.step, first.multi_step, first.agent_result) == ("checkpoint_1", True, {"n": 1})
    assert (second.step, second.agent_result, second.agent_execution) == (
        "checkpoint_2",
        None,
        None,
    )

    (one_shot,) = _executed_from(TRIAL, {"agent_result": {"n": 3}}, ["checkpoint_1"])
    assert (one_shot.step, one_shot.multi_step, one_shot.agent_result) == (
        "checkpoint_1",
        False,
        {"n": 3},
    )


@pytest.mark.parametrize(
    ("result", "oracle_steps", "reason"),
    [
        ({"step_results": "checkpoint_1"}, [], "not a list"),
        ({"step_results": [{"agent_result": {}}]}, [], "no step_name"),
        ({"step_results": ["checkpoint_1"]}, [], "no step_name"),
        ({}, [], "exactly one oracle record"),
        ({}, ["checkpoint_1", "checkpoint_2"], "exactly one oracle record"),
    ],
)
def test_a_trial_whose_executed_steps_cannot_be_known_is_refused(
    result: dict[str, object], oracle_steps: list[str], reason: str
) -> None:
    with pytest.raises(TrialInputError, match=reason):
        _executed_from(TRIAL, result, oracle_steps)


def test_the_plan_follows_the_recorded_steps_and_both_must_be_prefixes() -> None:
    available = [("checkpoint_1", "c1"), ("checkpoint_2", "c2"), ("checkpoint_3", "c3")]
    planned = _plan(TRIAL, ["checkpoint_1", "checkpoint_2"], available, ["checkpoint_1"])
    assert planned == available[:2]
    for names, ran in (
        ([], []),
        (["checkpoint_2"], []),
        (["checkpoint_1", "checkpoint_2", "checkpoint_3", "checkpoint_4"], []),
        (["checkpoint_1", "checkpoint_2"], ["checkpoint_2"]),
    ):
        with pytest.raises(TrialInputError, match="not a prefix"):
            _plan(TRIAL, names, available, ran)


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"verifier": {"kwargs": {"oracle_bundle": "/b"}}}, Path("/b")),
        ({"verifier": {"kwargs": {"oracle_bundle": ""}}}, None),
        ({"verifier": {"kwargs": {}}}, None),
        ({"verifier": "x"}, None),
        ({}, None),
    ],
)
def test_the_bundle_a_verifier_was_configured_with_is_read_from_its_kwargs(
    config: dict[str, object], expected: Path | None
) -> None:
    assert _oracle_bundle_of(config) == expected


def test_the_bundle_is_the_override_then_the_records_then_the_configured_one() -> None:
    records = _oracle_records("d1")
    recorded_root = Path(records["checkpoint_1"].bundle.root)
    assert _bundle_root(TRIAL, records, Path("/moved"), lambda: Path("/c")) == Path("/moved")
    assert _bundle_root(TRIAL, records, None, lambda: Path("/c")) == recorded_root
    assert _bundle_root(TRIAL, {}, None, lambda: Path("/c")) == Path("/c")
    with pytest.raises(TrialInputError, match="--bundle"):
        _bundle_root(TRIAL, {}, None, lambda: None)
    with pytest.raises(TrialInputError, match="several bundles"):
        _bundle_root(TRIAL, _oracle_records("d1", "d2"), None, lambda: None)


def test_a_bundle_must_match_the_digest_the_records_were_graded_with() -> None:
    records = _oracle_records("d1")
    cache = BundleCache(loader=lambda _: _bundle("d1"))
    assert _bundle_of(TRIAL, records, None, cache).digest() == "d1"
    moved = BundleCache(loader=lambda _: _bundle("other"))
    with pytest.raises(TrialInputError, match="does not match the digest"):
        _bundle_of(TRIAL, records, None, moved)
    # Nothing was graded: there is no digest to contradict.
    assert _bundle_of(TRIAL, {}, Path("/b"), moved).digest() == "other"


def test_bundles_load_once_per_location_and_an_unloadable_one_is_unavailable() -> None:
    loads: list[Path] = []

    def load(root: Path) -> Any:
        loads.append(root)
        if root.name == "broken":
            raise BundleError("malformed manifest")
        return _bundle("d1")

    cache = BundleCache(loader=load)
    assert cache.get(Path("/b/good"))[1] == "d1"
    assert cache.get(Path("/b/good"))[1] == "d1"
    assert len(loads) == 1
    with pytest.raises(BundleUnavailableError, match="malformed manifest"):
        cache.get(Path("/b/broken"))


def test_records_must_name_the_problem_of_the_bundle() -> None:
    _check_problems(TRIAL, {"toy"}, _bundle("d", "toy"))
    _check_problems(TRIAL, set(), _bundle("d", "toy"))
    with pytest.raises(TrialInputError, match="other problems"):
        _check_problems(TRIAL, {"toy", "other"}, _bundle("d", "toy"))


def test_a_graded_snapshot_must_be_present_and_unchanged() -> None:
    path = Path("steps/checkpoint_1/artifacts")
    assert _checked_snapshot(path, "d1", "d1") == (path, "d1")
    assert _checked_snapshot(path, "d1", None) == (path, "d1")  # never graded: nothing to compare
    assert _checked_snapshot(path, None, None) == (None, None)
    with pytest.raises(TrialInputError, match="changed since grading"):
        _checked_snapshot(path, "d2", "d1")
    with pytest.raises(TrialInputError, match="graded snapshot missing"):
        _checked_snapshot(path, None, "d1")


def test_a_harbor_config_names_the_task_whose_steps_are_the_plan() -> None:
    toml = '[[steps]]\nname = "checkpoint_1"\n\n[[steps]]\nname = "checkpoint_2"\n'
    config = {"task": {"path": "/tasks/toy"}}
    assert _task_plan(TRIAL, config, lambda path: toml) == ("checkpoint_1", "checkpoint_2")
    assert _task_plan(TRIAL, {"task": {"path": ""}}, lambda path: toml) is None
    assert _task_plan(TRIAL, {}, lambda path: toml) is None

    def unreadable(path: Path) -> str:
        raise OSError("gone")

    for read in (unreadable, lambda path: "[[steps]]\nnot_name = 1\n", lambda path: "= not toml"):
        with pytest.raises(TrialInputError, match="cannot read planned task steps"):
            _task_plan(TRIAL, config, read)


def test_an_invalid_retained_trial_record_is_refused_and_a_valid_one_gives_the_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = Path("records/t.json")
    with pytest.raises(TrialInputError, match="invalid trial record"):
        _trial_record_steps(record, {"kind": "not-a-trial"})
    fake = SimpleNamespace(
        model_validate=lambda doc: SimpleNamespace(planned_steps=tuple(doc["plan"]))
    )
    monkeypatch.setattr("raidar.scoring.framework.trial_inputs.TrialRecord", fake)
    assert _trial_record_steps(record, {"plan": ["checkpoint_1"]}) == ("checkpoint_1",)


def test_a_problems_entry_file_becomes_scorer_config_only_when_it_has_one() -> None:
    with_entry = SimpleNamespace(manifest=SimpleNamespace(entry_file="app/main.py"))
    without = SimpleNamespace(manifest=SimpleNamespace(entry_file=None))
    assert problem_config(with_entry) == {"entry_file": "app/main.py"}  # type: ignore[arg-type]
    assert problem_config(without) == {}  # type: ignore[arg-type]


def test_a_steps_inputs_are_its_graded_snapshot_trace_and_blinded_usage(tmp_path: Path) -> None:
    graded = memory_record("checkpoint_1", _grade_of("checkpoint_1"))
    snapshot = tmp_path / graded.snapshot.stored_path
    snapshot.mkdir(parents=True)
    (snapshot / "main.py").write_text("x = 1\n")
    record = graded.model_copy(
        update={"snapshot": graded.snapshot.model_copy(update={"digest": tree_digest(snapshot)})}
    )
    trace = tmp_path / "steps" / "checkpoint_1" / "agent"
    trace.mkdir(parents=True)
    usage = {
        "n_input_tokens": 3,
        "model_usage": {"label": {"n_input_tokens": 3}},
        "metadata": {"workflow": {"jev-judge/1": {}}},
    }
    timing = {"started_at": "2026-09-30T00:00:00Z", "finished_at": "2026-09-30T00:00:02Z"}
    executed = _Executed("checkpoint_1", True, usage, timing)

    inputs = _step_inputs(tmp_path, executed, "planned_cp", record)

    assert (inputs.snapshot, inputs.snapshot_digest) == (snapshot, tree_digest(snapshot))
    assert inputs.trace_dir == trace
    assert inputs.checkpoint == "checkpoint_1"  # the record's, not the plan's
    assert inputs.judged is True
    assert inputs.agent_execution_s == 2.0
    assert inputs.agent_result is not None
    assert "label" not in json.dumps(inputs.agent_result)
    assert inputs.record_digest is not None
    assert inputs.record_digest == _record_digest(step_record_dir(tmp_path, "checkpoint_1"), record)


def test_an_ungraded_one_shot_step_has_no_snapshot_and_no_record_digest(tmp_path: Path) -> None:
    inputs = _step_inputs(tmp_path, _Executed("checkpoint_1", False, None), "planned_cp", None)
    assert (inputs.snapshot, inputs.snapshot_digest, inputs.record_digest) == (None, None, None)
    assert (inputs.trace_dir, inputs.checkpoint, inputs.reached) == (None, "planned_cp", True)
    skipped = _unreached("checkpoint_2", "planned_cp2")
    assert (skipped.reached, skipped.record, skipped.snapshot, skipped.agent_result) == (
        False,
        None,
        None,
        None,
    )


def test_the_plan_is_read_from_the_trial_record_else_the_configured_task(tmp_path: Path) -> None:
    trial = tmp_path / "job" / "trials" / "t"
    trial.mkdir(parents=True)
    with pytest.raises(TrialInputError, match="no retained trial record"):
        _recorded_steps(trial)

    task = tmp_path / "task"
    task.mkdir()
    (task / "task.toml").write_text('[[steps]]\nname = "checkpoint_1"\n')
    (trial / "config.json").write_text(json.dumps({"task": {"path": str(task)}}))
    assert _recorded_steps(trial) == ("checkpoint_1",)

    records = tmp_path / "job" / TRIAL_RECORDS_DIR
    records.mkdir()
    (records / "t.json").write_text(json.dumps({"kind": "not-a-trial"}))
    with pytest.raises(TrialInputError, match="invalid trial record"):
        _recorded_steps(trial)


def test_the_bundle_a_trial_was_graded_with_is_found_from_its_verifier_config(
    tmp_path: Path,
) -> None:
    assert _configured_bundle(tmp_path) is None
    (tmp_path / "lock.json").write_text(json.dumps({"verifier": {}}))
    (tmp_path / "config.json").write_text(
        json.dumps({"verifier": {"kwargs": {"oracle_bundle": "/bundles/toy"}}})
    )
    assert _configured_bundle(tmp_path) == Path("/bundles/toy")
    assert _load_records(tmp_path, [_Executed("checkpoint_1", True, None)]) == {}


def test_every_planned_checkpoint_is_a_step_reached_or_not(tmp_path: Path) -> None:
    trial = tmp_path / "job" / "trials" / "t"
    trial.mkdir(parents=True)
    task = tmp_path / "task"
    task.mkdir()
    names = ["checkpoint_1", "checkpoint_2", "checkpoint_3"]
    (task / "task.toml").write_text("".join(f'[[steps]]\nname = "{n}"\n' for n in names))
    (trial / "config.json").write_text(json.dumps({"task": {"path": str(task)}}))
    steps = [SimpleNamespace(name=name, checkpoint=f"c{index}") for index, name in enumerate(names)]
    bundle: Any = SimpleNamespace(manifest=SimpleNamespace(steps=steps))

    planned = _planned_steps(trial, [_Executed("checkpoint_1", True, None)], {}, bundle)

    assert [(s.step, s.reached, s.checkpoint) for s in planned] == [
        ("checkpoint_1", True, "c0"),
        ("checkpoint_2", False, "c1"),
        ("checkpoint_3", False, "c2"),
    ]


def _tiny_trial(root: Path, result: dict[str, Any], steps: list[str]) -> Path:
    """A trial of just the files the loader reads: result, task plan, ungraded."""
    trial = root / "job" / "trials" / "t"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text(json.dumps(result))
    task = root / "task"
    task.mkdir()
    (task / "task.toml").write_text("".join(f'[[steps]]\nname = "{n}"\n' for n in steps))
    (trial / "config.json").write_text(json.dumps({"task": {"path": str(task)}}))
    return trial


def _plan_bundle(*names: str) -> Any:
    steps = [SimpleNamespace(name=name, checkpoint=f"c-{name}") for name in names]
    manifest = SimpleNamespace(problem="toy", entry_file="app.py", steps=steps)
    return SimpleNamespace(digest=lambda: "d1", manifest=manifest)


def test_a_stored_trial_loads_as_every_planned_checkpoint_under_the_bundles_problem(
    tmp_path: Path,
) -> None:
    names = ["checkpoint_1", "checkpoint_2"]
    executed = {"step_name": "checkpoint_1", "agent_result": {"n_input_tokens": 1}}
    result = {"step_results": [executed]}
    trial = _tiny_trial(tmp_path, result, names)
    cache = BundleCache(loader=lambda _: _plan_bundle(*names))

    ctx = load_trial_context(
        trial, config={"k": 1}, bundle_override=Path("/bundles/toy"), bundle_cache=cache
    )

    expected_config = ("toy", {"k": 1}, {"entry_file": "app.py"})
    assert (ctx.problem, ctx.config, ctx.problem_config) == expected_config
    assert [(s.step, s.reached, s.checkpoint) for s in ctx.steps] == [
        ("checkpoint_1", True, "c-checkpoint_1"),
        ("checkpoint_2", False, "c-checkpoint_2"),
    ]
    assert ctx.steps[0].record is None
    assert ctx.steps[0].agent_result is not None


def test_a_one_shot_trial_runs_the_step_of_its_only_oracle_record(tmp_path: Path) -> None:
    trial = _tiny_trial(tmp_path, {"agent_result": {"n_input_tokens": 2}}, ["checkpoint_1"])
    (trial / VERIFIER_RECORD_DIR / "steps" / "checkpoint_1").mkdir(parents=True)
    cache = BundleCache(loader=lambda _: _plan_bundle("checkpoint_1"))

    ctx = load_trial_context(trial, bundle_override=Path("/bundles/toy"), bundle_cache=cache)

    assert [(s.step, s.reached) for s in ctx.steps] == [("checkpoint_1", True)]


def test_a_stored_trial_without_a_findable_bundle_is_refused(tmp_path: Path) -> None:
    result = {"step_results": [{"step_name": "checkpoint_1"}]}
    trial = _tiny_trial(tmp_path, result, ["checkpoint_1"])
    with pytest.raises(TrialInputError, match="--bundle"):
        load_trial_context(trial, bundle_cache=BundleCache(loader=lambda _: _plan_bundle()))
