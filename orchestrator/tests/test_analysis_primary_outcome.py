"""``analysis.primary_outcome`` and the lock-to-report path, in memory.

Unset, the primary is the progression's default (``solve_rate_solvable`` under
``continue``, the raw ``solve_rate`` beside it). Set, it must be a known trial outcome; it is
analysis configuration (the lock's ``analysis`` section), so ``lock_sha256``
does not move; and an errata-applied primary is always reported with its raw
counterpart beside it.

The same file drives the loading decisions (jobs, lock, stored Assessments) and the
``raidar analyze`` command through injected readers and ``CliRunner``; the real store and run
directories are the slow ``test_analysis_command`` seams.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import raidar.cli.analyze as analyze_cli
from raidar.cli import main
from raidar.design.experiment.loader import load_experiment
from raidar.design.experiment.lock import seal_lock
from raidar.design.experiment.validation import SpecValidationError
from raidar.engine.job.assessment import lock_rescore_config
from raidar.evaluation.analysis import load as load_module
from raidar.evaluation.analysis import lookup as lookup_module
from raidar.evaluation.analysis.assessed import (
    PROCESS_COMPLIANCE_REF,
    VERIFICATION_EFFORT_REF,
    verification_effort_ref,
)
from raidar.evaluation.analysis.effects import Settings
from raidar.evaluation.analysis.load import LoadRequest, load_inputs
from raidar.evaluation.analysis.lookup import AssessmentLookup, assessment_config
from raidar.evaluation.analysis.markdown import render_markdown
from raidar.evaluation.analysis.outcomes import PoolFinding, PoolingReport, Value
from raidar.evaluation.analysis.report import DevRun, analyse
from raidar.evaluation.analysis.report import PlannedTrial as PlannedRow
from raidar.evaluation.analysis.sources import (
    AnalysisInputError,
    DevOptions,
    JobDir,
    check_jobs,
    default_lock,
    find_job_manifest,
    job_options,
    planned_trials,
    read_job,
    read_lock,
    select_records,
    stored_bundle,
)
from raidar.scoring.catalog.outcomes import (
    PRIMARY_FROM_LOCK,
    RAW_COUNTERPART,
    SOLVE_RATE,
    SOLVE_RATE_ERRATA,
    SOLVE_RATE_SOLVABLE,
    primary_metric,
    primary_source,
)
from raidar.scoring.framework.rescore import RescoreError
from raidar.scoring.framework.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)
from raidar.scoring.framework.trial_inputs import BundleUnavailableError, TrialInputError
from tests.analysis_support import (
    OUTCOME_TYPES,
    analysis_lock,
    inputs,
    paired_arms,
    trial_record,
)
from tests.experiment_spec_support import (
    experiment_document,
    write_experiment,
)

SETTINGS = Settings(
    outcome_types=OUTCOME_TYPES,
    min_pairs=5,
    max_exclusion_fraction=0.2,
    max_exclusion_difference=0.5,
    bootstrap_samples=2000,
    seed=7,
)


def _outcomes() -> list[Any]:
    raw = paired_arms(SOLVE_RATE, [0.25] * 6, [0.5] * 6)
    errata = paired_arms(SOLVE_RATE_ERRATA, [0.5] * 6, [0.5, 0.625, 0.5, 0.625, 0.5, 0.625])
    merged = []
    for left, right in zip(raw, errata, strict=True):
        merged.append(left.model_copy(update={"metrics": {**left.metrics, **right.metrics}}))
    return merged


# ---- the experiment schema -------------------------------------------------------------


def test_primary_outcome_is_validated_against_known_trial_outcomes(tmp_path: Path) -> None:
    document = experiment_document()
    document["analysis"]["primary_outcome"] = SOLVE_RATE_ERRATA
    spec = load_experiment(write_experiment(tmp_path / "ok", document)).spec
    assert spec.analysis.primary_outcome == SOLVE_RATE_ERRATA
    assert spec.to_json()["analysis"]["primary_outcome"] == SOLVE_RATE_ERRATA

    plain = load_experiment(write_experiment(tmp_path / "plain", experiment_document())).spec
    assert plain.analysis.primary_outcome is None
    assert "primary_outcome" not in plain.to_json()["analysis"]

    for bad in ("vibes", "gate.first_pass_clean_rate"):
        document["analysis"]["primary_outcome"] = bad
        with pytest.raises(SpecValidationError) as caught:
            load_experiment(write_experiment(tmp_path / bad, document))
        issues = {issue.loc: issue.message for issue in caught.value.issues}
        assert f"unknown primary outcome {bad!r}" in issues["analysis.primary_outcome"]


# ---- the analysis report ---------------------------------------------------------------


def test_the_default_primary_is_the_solvable_set_rate_with_raw_beside_it() -> None:
    solvable = paired_arms(SOLVE_RATE_SOLVABLE, [0.5] * 6, [0.75] * 6)
    outcomes = [
        raw.model_copy(update={"metrics": {**raw.metrics, **extra.metrics}})
        for raw, extra in zip(_outcomes(), solvable, strict=True)
    ]
    report = analyse(inputs(outcomes, 6), SETTINGS)

    assert (report.primary_metric, report.primary_source) == (
        SOLVE_RATE_SOLVABLE,
        "default for continue progression",
    )
    (comparison,) = report.comparisons
    assert comparison.primary.metric == SOLVE_RATE_SOLVABLE
    assert comparison.primary_raw is not None
    assert comparison.primary_raw.metric == SOLVE_RATE
    markdown = render_markdown(report)
    assert "**Primary outcome** `solve_rate_solvable`" in markdown
    assert "**Raw (beside the primary)** `solve_rate`" in markdown
    assert primary_metric("halt_on_failure") == SOLVE_RATE_SOLVABLE


def test_audit_scope_and_flags_are_reported_beside_reference_success() -> None:
    audit = {
        "status": "measured",
        "assertions": {"audited": 2, "total": 10},
        "flagged_count": 1,
        "flagged": ["checkpoint_1::wording"],
    }
    report = analyse(
        dataclasses.replace(inputs(_outcomes(), 6), spec_determinacy={"toy@1": audit}),
        SETTINGS,
    )
    assert report.spec_determinacy["toy@1"]["assertions"] == {"audited": 2, "total": 10}
    markdown = render_markdown(report)
    assert "Reference-solvable SCB success" in markdown
    assert "| toy@1 | 2/10 | measured | 1 | checkpoint_1::wording |" in markdown
    unmeasured = analyse(inputs(_outcomes(), 6), SETTINGS)
    assert "| toy@1 | 0/unknown | unmeasured | unmeasured |" in render_markdown(unmeasured)


def test_a_primary_without_solvable_sets_is_unavailable_never_zero() -> None:
    outcomes = [
        outcome.model_copy(
            update={
                "metrics": {
                    **outcome.metrics,
                    SOLVE_RATE_SOLVABLE: Value.missing("unavailable", "no solvable set"),
                }
            }
        )
        for outcome in _outcomes()
    ]
    report = analyse(inputs(outcomes, 6), SETTINGS)

    (comparison,) = report.comparisons
    assert comparison.primary.verdict.value == "insufficient"
    assert comparison.primary_raw is not None
    assert comparison.primary_raw.verdict.value != "insufficient"


def test_a_preregistered_errata_primary_is_labelled_with_raw_beside_it() -> None:
    report = analyse(
        dataclasses.replace(inputs(_outcomes(), 6), primary_outcome=SOLVE_RATE_ERRATA), SETTINGS
    )

    assert (report.primary_metric, report.primary_source) == (SOLVE_RATE_ERRATA, PRIMARY_FROM_LOCK)
    (comparison,) = report.comparisons
    assert comparison.primary.metric == SOLVE_RATE_ERRATA
    assert comparison.primary_raw is not None
    assert comparison.primary_raw.metric == SOLVE_RATE
    assert comparison.primary_raw.mean_delta == pytest.approx(0.25)
    markdown = render_markdown(report)
    assert "**Primary outcome** `solve_rate_errata`" in markdown
    assert "**Raw (beside the primary)** `solve_rate`" in markdown
    assert "| Outcome | Problem | Both | Baseline only |" in markdown
    assert "| final_solved | toy@1 |" in markdown
    assert "| final_solved_errata | toy@1 |" in markdown
    assert RAW_COUNTERPART[SOLVE_RATE_ERRATA] == SOLVE_RATE
    assert primary_source("continue", SOLVE_RATE_ERRATA) == PRIMARY_FROM_LOCK


def test_an_errata_primary_without_errata_still_has_a_primary_effect() -> None:
    outcomes = paired_arms(SOLVE_RATE, [0.25] * 5, [0.5] * 5)

    report = analyse(
        dataclasses.replace(inputs(outcomes, 5), primary_outcome=SOLVE_RATE_ERRATA), SETTINGS
    )

    (comparison,) = report.comparisons
    assert comparison.primary.metric == SOLVE_RATE_ERRATA
    assert comparison.primary.verdict.value == "insufficient"
    assert comparison.primary_raw is not None
    assert comparison.primary_raw.metric == SOLVE_RATE


def test_arms_with_no_shared_attempt_are_refused_not_crashed() -> None:
    base = inputs(paired_arms(SOLVE_RATE, [0.25] * 5, [0.5] * 5), 5)
    planned = tuple(
        dataclasses.replace(trial, attempt=trial.attempt + 10)
        if trial.arm != base.baseline_arm
        else trial
        for trial in base.planned
    )
    with pytest.raises(ValueError, match="no attempt is planned for both"):
        analyse(dataclasses.replace(base, planned=planned), SETTINGS)


def test_the_lock_declared_verification_effort_scorer_is_read() -> None:
    assert VERIFICATION_EFFORT_REF == "verification-effort@1"
    assert verification_effort_ref(["correctness@10", "verification-effort@1"]) == (
        "verification-effort@1"
    )
    assert verification_effort_ref(["correctness@10"]) is None


# ---- the manipulation check follows the declared process scorer -------------------------


@pytest.mark.parametrize("declared", [True, False])
def test_the_manipulation_check_exists_only_for_a_declared_process_compliance_scorer(
    declared: bool,
) -> None:
    outcomes = paired_arms(SOLVE_RATE, [0.25] * 5, [0.5] * 5)
    process = {outcome.trial_id: None for outcome in outcomes} if declared else None
    loaded = dataclasses.replace(inputs(outcomes, 5), process_results=process)

    (comparison,) = analyse(loaded, SETTINGS).comparisons

    if not declared:
        assert comparison.manipulation is None
    else:
        # No trial has a trajectory, so no check is decided in either arm.
        assert comparison.manipulation is not None
        assert comparison.manipulation.verdict == "insufficient_data"


# ---- reading jobs and the lock ----------------------------------------------------------

PROBLEM = "scb/toy@1"


def _job(name: str, *records: Any) -> JobDir:
    return JobDir(Path(name), {"job_id": name}, {}, tuple(records))


def test_a_valid_rerun_replaces_an_invalid_trial_but_two_valid_ones_conflict() -> None:
    first = _job("run-1", trial_record("t", "a", 0, valid=False))
    rerun = _job("run-2", trial_record("t", "a", 0))
    chosen, replaced = select_records([first, rerun])
    assert chosen["t"][0] is rerun
    assert replaced == ("run-1/t",)
    chosen, replaced = select_records([rerun, first])
    assert chosen["t"][0] is rerun
    assert replaced == ("run-1/t",)
    with pytest.raises(AnalysisInputError, match="valid in both"):
        select_records([rerun, rerun])


def _dev_job(name: str, plan: DevOptions, recorded: DevOptions | None = None) -> JobDir:
    recorded = plan if recorded is None else recorded
    record = trial_record(
        "t", "a", 0, truncated_to=recorded.max_checkpoints, agent_override=recorded.agent_override
    )
    options = {"max_checkpoints": plan.max_checkpoints, "agent_override": plan.agent_override}
    return JobDir(Path(name), {"job_id": name}, {"options": options}, (record,))


def test_jobs_share_their_development_options_or_are_refused() -> None:
    plain = _dev_job("plain", DevOptions())
    truncated = _dev_job("dev", DevOptions(max_checkpoints=2, agent_override="noop"))

    assert job_options([plain, _dev_job("again", DevOptions())], allow_dev_jobs=False) == (
        DevOptions()
    )
    with pytest.raises(AnalysisInputError, match="not arm evidence"):
        job_options([truncated], allow_dev_jobs=False)
    shared = job_options([truncated], allow_dev_jobs=True)
    assert shared.active
    assert (shared.max_checkpoints, shared.agent_override) == (2, "noop")
    assert "2" in shared.describe()
    assert "noop" in shared.describe()
    with pytest.raises(AnalysisInputError, match="different development options"):
        job_options([truncated, plain], allow_dev_jobs=True)
    unrecorded = _dev_job("odd", DevOptions(max_checkpoints=2), DevOptions())
    with pytest.raises(AnalysisInputError, match="but the plan declares"):
        job_options([unrecorded], allow_dev_jobs=True)


def _checked(name: str, lock: dict[str, Any], *, trials: tuple[str, ...] = ("t",)) -> JobDir:
    plan = {"trials": [{"trial_id": trial, "arm": "a"} for trial in trials]}
    return JobDir(Path(name), {"job_id": name, "lock_sha256": lock["lock_sha256"]}, plan, ())


def test_jobs_of_another_lock_or_plan_are_refused_before_pooling() -> None:
    lock = analysis_lock(["correctness@10"])
    one, two = _checked("one", lock), _checked("two", lock)
    assert check_jobs([one, two], lock).unmeasured == ("one", "two")

    foreign = JobDir(Path("alien"), {"lock_sha256": "other"}, {"trials": []}, ())
    with pytest.raises(AnalysisInputError, match="executed under lock"):
        check_jobs([one, foreign], lock)
    with pytest.raises(AnalysisInputError, match="different trials"):
        check_jobs([one, _checked("two", lock, trials=("t", "u"))], lock)
    with pytest.raises(AnalysisInputError, match="unknown fields"):
        check_jobs([one, two], lock, ["no-such-override"])


def test_the_plan_lists_the_planned_trials_and_the_job_locates_its_trial_directories() -> None:
    plan = {"trials": [{"trial_id": "t", "arm": "guided", "attempt": "2", "problem": PROBLEM}]}
    assert planned_trials(plan) == (PlannedRow("t", "guided", 2, PROBLEM),)
    assert planned_trials({}) == ()

    job = JobDir(Path("jobs/run"), {}, {}, ())
    record = trial_record("t", "guided", 0)
    assert job.job_id == "run"
    assert job.trial_dir(record) == Path("jobs/run/trials/t")
    absolute = Path("/abs/trial").resolve()
    assert job.trial_dir(record.model_copy(update={"trial_dir": str(absolute)})) == absolute


def test_a_job_directory_reads_its_manifest_plan_and_records(tmp_path: Path) -> None:
    job = tmp_path / "job"
    (job / "trial-records").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"job_id": "j"}))
    (job / "plan.json").write_text(json.dumps({"trials": []}))
    record = trial_record("t", "baseline", 0)
    (job / "trial-records" / "t.json").write_text(record.model_dump_json())

    read = read_job(job)
    assert (read.job_id, [item.trial.trial_id for item in read.records]) == ("j", ["t"])

    (job / "trial-records" / "u.json").write_text("{}")
    with pytest.raises(AnalysisInputError, match="invalid trial record"):
        read_job(job)
    (job / "trial-records" / "u.json").unlink()
    (job / "plan.json").write_text("[]")
    with pytest.raises(AnalysisInputError, match="not a JSON object"):
        read_job(job)
    (job / "job.json").unlink()
    with pytest.raises(AnalysisInputError, match="cannot read"):
        read_job(job)


def test_a_job_is_found_from_itself_or_from_one_of_its_trial_directories(tmp_path: Path) -> None:
    job = tmp_path / "job"
    trial_dir = job / "trials" / "t1"
    trial_dir.mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"job_id": "j"}))

    assert find_job_manifest(job) == (job.resolve(), {"job_id": "j"})
    assert find_job_manifest(trial_dir) == (job.resolve(), {"job_id": "j"})
    assert find_job_manifest(tmp_path / "elsewhere" / "a" / "b" / "c") is None


def test_a_lock_must_be_sealed_and_unaltered(tmp_path: Path) -> None:
    lock = analysis_lock(["correctness@10"])
    path = tmp_path / "pilot.lock.json"
    path.write_text(json.dumps(lock))
    assert read_lock(path)["lock_sha256"] == lock["lock_sha256"]

    tampered = json.loads(path.read_text())
    tampered["spec"]["analysis"]["baseline_arm"] = "guided"
    path.write_text(json.dumps(tampered))
    with pytest.raises(AnalysisInputError, match="not a valid lock"):
        read_lock(path)


def test_a_job_resolves_only_the_lock_its_manifest_names_and_otherwise_asks_for_one(
    tmp_path: Path,
) -> None:
    lock = analysis_lock(["correctness@10"])
    experiment_file = tmp_path / "pilot.yaml"
    lock_path = tmp_path / "pilot.lock.json"
    lock_path.write_text(json.dumps(lock))
    manifest = {"job_id": "j", "lock_sha256": lock["lock_sha256"]}

    def job(**extra: object) -> JobDir:
        return JobDir(tmp_path / "job", {**manifest, **extra}, {}, ())

    assert default_lock(job(experiment_file=str(experiment_file))) == lock_path
    with pytest.raises(AnalysisInputError, match="names no experiment_file; pass --lock"):
        default_lock(job())
    lock_path.write_text(json.dumps(analysis_lock(["correctness@10"], experiment="another")))
    with pytest.raises(AnalysisInputError, match="another experiment.*pass --lock"):
        default_lock(job(experiment_file=str(experiment_file)))
    lock_path.unlink()
    with pytest.raises(AnalysisInputError, match="pass --lock"):
        default_lock(job(experiment_file=str(experiment_file)))


class _ProblemStore:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def load_manifest(self, problem_id: object) -> Any:
        if self.error is not None:
            raise self.error
        return type("Manifest", (), {"content_key": staticmethod(lambda: "key-1")})

    def content_dir(self, problem_id: object, key: str) -> Path:
        return Path("content") / key


def test_the_problem_stores_oracle_is_the_bundle_of_a_moved_trial() -> None:
    found = stored_bundle(_ProblemStore(), PROBLEM)  # type: ignore[arg-type]
    assert found == Path("content/key-1/oracle")
    with pytest.raises(TrialInputError, match="has no materialised bundle"):
        stored_bundle(_ProblemStore(ValueError("unknown")), PROBLEM)  # type: ignore[arg-type]


# ---- finding a trial's stored Assessments -----------------------------------------------


class _Registry:
    def get(self, ref: str) -> str:
        return ref


class _Store:
    def __init__(self, found: dict[str, AssessResult]) -> None:
        self.found = found

    def get(self, key: str) -> Any:
        result = self.found.get(key)
        return None if result is None else type("Assessment", (), {"result": result})


def _stored(value: float = 1.0) -> AssessResult:
    """A stored result; ``value`` tells one scorer's result from another's."""
    measured = Observation(
        metric="reached", subject=TRAJECTORY_SUBJECT, status=AssessStatus.OK, value=value
    )
    return AssessResult(status=AssessStatus.OK, observations=(measured,))


def _lookup(
    monkeypatch: pytest.MonkeyPatch, loads: list[Any], found: dict[str, AssessResult]
) -> tuple[AssessmentLookup, list[Any]]:
    """A lookup whose trial contexts come from ``loads`` (an exception is raised, in order)."""
    overrides: list[Any] = []

    def load_context(trial_dir: Path, *, bundle_override: Path | None = None, **_: object) -> Any:
        overrides.append(bundle_override)
        loaded = loads[len(overrides) - 1]
        if isinstance(loaded, Exception):
            raise loaded
        return loaded

    monkeypatch.setattr(lookup_module, "load_trial_context", load_context)
    monkeypatch.setattr(lookup_module, "context_inputs", lambda ctx: {"context": ctx})
    monkeypatch.setattr(lookup_module, "scorer_identity", lambda scorer: f"identity:{scorer}")
    monkeypatch.setattr(
        lookup_module,
        "store_key",
        lambda scorer, config, inputs, *, identity: type("Key", (), {"key": scorer}),
    )
    lookup = AssessmentLookup(
        store=_Store(found),  # type: ignore[arg-type]
        registry=_Registry(),  # type: ignore[arg-type]
        scorer_refs=("a@1", "b@1"),
        stored_bundle=lambda problem: Path("problems") / "oracle",
    )
    return lookup, overrides


def _stored_trial(tmp_path: Path) -> Path:
    trial_dir = tmp_path / "t"
    trial_dir.mkdir()
    (trial_dir / "result.json").write_text("{}")
    return trial_dir


CONFIG = lock_rescore_config(analysis_lock(["correctness@10"]))


def test_a_trial_is_assessed_by_the_scorers_whose_keys_the_store_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = _stored()
    lookup, overrides = _lookup(monkeypatch, ["context"], {"a@1": stored})

    found = lookup.results(_stored_trial(tmp_path), CONFIG, problem=PROBLEM)

    assert found == {"a@1": stored, "b@1": None}
    assert overrides == [None]


def test_a_trial_without_a_result_file_has_no_assessments_to_find(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup, overrides = _lookup(monkeypatch, [], {"a@1": _stored()})

    found = lookup.results(tmp_path / "absent", CONFIG, problem=PROBLEM)

    assert found == {"a@1": None, "b@1": None}
    assert overrides == []


def test_a_moved_bundle_is_read_from_the_problem_store_once_the_recorded_root_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = _stored()
    lookup, overrides = _lookup(
        monkeypatch, [BundleUnavailableError("moved"), "context"], {"b@1": stored}
    )

    found = lookup.results(_stored_trial(tmp_path), CONFIG, problem=PROBLEM)

    assert found == {"a@1": None, "b@1": stored}
    assert overrides == [None, Path("problems") / "oracle"]


@pytest.mark.parametrize(
    "loads",
    [
        [BundleUnavailableError("moved"), TrialInputError("digest differs")],
        [TrialInputError("no snapshot")],
    ],
    ids=["moved-and-refused", "unloadable"],
)
def test_inputs_that_cannot_be_loaded_refuse_a_valid_trial_and_leave_an_invalid_one_unassessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loads: list[Any]
) -> None:
    trial_dir = _stored_trial(tmp_path)
    lookup, _ = _lookup(monkeypatch, loads, {"a@1": _stored()})
    with pytest.raises(AnalysisInputError, match=r"trial t7 .* cannot be assessed") as refused:
        lookup.valid_results(trial_dir, CONFIG, trial_id="t7", valid=True, problem=PROBLEM)
    assert str(loads[-1]) in str(refused.value)

    lookup, _ = _lookup(monkeypatch, loads, {"a@1": _stored()})
    unassessed = lookup.valid_results(
        trial_dir, CONFIG, trial_id="t7", valid=False, problem=PROBLEM
    )
    assert unassessed == {"a@1": None, "b@1": None}
    assert trial_dir in lookup.input_errors


def test_the_assessment_settings_come_from_the_lock_unless_a_config_file_overrides_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = analysis_lock(["correctness@10"])
    assert assessment_config(None, lock) == CONFIG

    override = tmp_path / "assessment-config.yaml"
    override.write_text("scorers: {}\n")
    assert assessment_config(override, lock).scorers == {}

    def unreadable(path: Path) -> None:
        raise RescoreError("cannot read the config")

    def invalid_lock(_: object) -> None:
        raise ValueError("unknown scorer")

    monkeypatch.setattr(lookup_module, "load_rescore_config", unreadable)
    with pytest.raises(AnalysisInputError, match="cannot read the config"):
        assessment_config(override, lock)
    monkeypatch.setattr(lookup_module, "lock_rescore_config", invalid_lock)
    with pytest.raises(AnalysisInputError, match="assessment settings are invalid"):
        assessment_config(None, lock)


# ---- loading jobs, the lock and stored results into analysis inputs ---------------------

THREE_STEPS = ("checkpoint_1", "checkpoint_2", "checkpoint_3")
NO_REGISTRY: Any = object()


def _pinned_lock(
    scorers: list[str],
    solvable: dict[str, dict[str, list[str]]],
    determinacy: dict[str, dict[str, object]] | None = None,
) -> dict[str, Any]:
    """A sealed lock pinning solvable sets (and audit facts) for the given problems."""
    lock = analysis_lock(scorers)
    problems = {
        ref: {
            "metadata": {
                "solvable": {"sha256": "ab" * 32, "steps": steps},
                "validation": {"spec_determinacy": (determinacy or {}).get(ref, {})},
            }
        }
        for ref, steps in solvable.items()
    }
    lock["execution"]["references"] = {"problem": problems}
    omitted = {"sections", "lock_sha256", "document_sha256"}
    unsealed = {key: value for key, value in lock.items() if key not in omitted}
    return seal_lock(unsealed)


def _toy_job(
    name: str, lock: dict[str, Any], *, valid: bool = True, options: DevOptions | None = None
) -> JobDir:
    options = options or DevOptions()
    records = tuple(
        trial_record(
            f"toy-{arm}-b{attempt}",
            arm,
            attempt,
            valid=valid,
            truncated_to=options.max_checkpoints,
            agent_override=options.agent_override,
        )
        for arm in ("baseline", "guided")
        for attempt in range(2)
    )
    plan = {
        "options": {
            "max_checkpoints": options.max_checkpoints,
            "agent_override": options.agent_override,
        },
        "trials": [
            {
                "trial_id": record.trial.trial_id,
                "arm": record.trial.arm,
                "attempt": record.trial.attempt,
                "problem": PROBLEM,
            }
            for record in records
        ],
    }
    manifest = {"job_id": name, "lock_sha256": lock["lock_sha256"]}
    return JobDir(Path(name), manifest, plan, records)


def _load(
    monkeypatch: pytest.MonkeyPatch,
    jobs: list[JobDir],
    lock: dict[str, Any],
    *,
    results: dict[str, dict[str, AssessResult | None]] | None = None,
    seen_refs: list[tuple[str, ...]] | None = None,
    allow_dev_jobs: bool = False,
    lock_path: Path | None = None,
) -> Any:
    """``load_inputs`` over in-memory jobs, an in-memory lock and stored results per trial."""
    by_path = {job.path: job for job in jobs}
    monkeypatch.setattr(load_module, "read_job", lambda path: by_path[path])
    monkeypatch.setattr(load_module, "read_lock", lambda path: lock)
    monkeypatch.setattr(load_module, "default_lock", lambda job: Path("default.lock.json"))

    def valid_results(
        self: AssessmentLookup, trial_dir: Path, config: object, *, trial_id: str, **_: object
    ) -> dict[str, AssessResult | None]:
        if seen_refs is not None:
            seen_refs.append(self.scorer_refs)
        return (results or {}).get(trial_id, {})

    monkeypatch.setattr(AssessmentLookup, "valid_results", valid_results)
    request = LoadRequest(
        job_dirs=tuple(by_path),
        store_root=Path("store"),
        registry=NO_REGISTRY,
        lock_path=lock_path,
        allow_dev_jobs=allow_dev_jobs,
    )
    return load_inputs(request)


def test_loading_assembles_jobs_plan_and_the_pinned_sets_of_the_planned_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = "scb/other@1"
    pins = {step: [f"{step}::case_0"] for step in THREE_STEPS}
    audit = {PROBLEM: {"audited": 4}}
    lock = _pinned_lock(["correctness@10"], {PROBLEM: pins, other: pins}, audit)
    first = _toy_job("run-1", lock, valid=False)
    rerun = _toy_job("run-2", lock)

    loaded, read = _load(monkeypatch, [first, rerun], lock)

    assert read is lock
    assert (loaded.experiment, loaded.lock_sha256) == ("pilot", lock["lock_sha256"])
    assert (loaded.kind, loaded.attempts, loaded.baseline_arm) == ("ab-test", 5, "baseline")
    assert (loaded.progression, loaded.rank_by, loaded.primary_outcome) == ("continue", None, None)
    assert loaded.jobs == ("run-1", "run-2")
    trials = sorted(record.trial.trial_id for record in rerun.records)
    assert [outcome.trial_id for outcome in loaded.outcomes] == trials
    assert not any(outcome.infra_error for outcome in loaded.outcomes)
    assert loaded.replaced == tuple(f"run-1/{trial}" for trial in trials)
    assert loaded.planned == tuple(
        PlannedRow(item["trial_id"], item["arm"], item["attempt"], PROBLEM)
        for item in first.plan["trials"]
    )
    # Only problems with planned trials are summarised, and only over their planned steps.
    assert loaded.solvable == {
        PROBLEM: {"sha256": "ab" * 32, "cases": {"checkpoint_1": 1, "checkpoint_2": 1}}
    }
    assert loaded.spec_determinacy == {PROBLEM: {"audited": 4}, other: {}}
    assert loaded.pooling is not None
    assert loaded.pooling.unmeasured == ("run-1", "run-2")
    assert (loaded.dev_job, loaded.process_results, loaded.gate_results) == (None, None, None)
    assert (loaded.process_scorer, loaded.gate_scorer) == (None, None)


@pytest.mark.parametrize(
    "declared",
    [
        [],
        [PROCESS_COMPLIANCE_REF],
        [VERIFICATION_EFFORT_REF],
        [PROCESS_COMPLIANCE_REF, VERIFICATION_EFFORT_REF],
    ],
)
def test_loading_reads_process_and_gate_results_only_for_the_declared_scorers(
    monkeypatch: pytest.MonkeyPatch, declared: list[str]
) -> None:
    lock = analysis_lock(["correctness@10", *declared])
    job = _toy_job("run-1", lock)
    process, gate = _stored(1.0), _stored(2.0)
    results: dict[str, dict[str, AssessResult | None]] = {
        record.trial.trial_id: {PROCESS_COMPLIANCE_REF: process, VERIFICATION_EFFORT_REF: gate}
        for record in job.records
    }
    seen: list[tuple[str, ...]] = []

    loaded, _ = _load(monkeypatch, [job], lock, results=results, seen_refs=seen)

    assert set(seen) == {("correctness@10", *declared)}
    trial_ids = [record.trial.trial_id for record in job.records]
    has_process, has_gate = PROCESS_COMPLIANCE_REF in declared, VERIFICATION_EFFORT_REF in declared
    assert loaded.process_results == (dict.fromkeys(trial_ids, process) if has_process else None)
    assert loaded.gate_results == (dict.fromkeys(trial_ids, gate) if has_gate else None)
    assert loaded.process_scorer == (PROCESS_COMPLIANCE_REF if has_process else None)
    assert loaded.gate_scorer == (VERIFICATION_EFFORT_REF if has_gate else None)


def test_development_jobs_are_loaded_only_when_allowed_and_labelled_not_scored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = analysis_lock(["correctness@10"])
    options = DevOptions(max_checkpoints=2, agent_override="noop")
    job = _toy_job("dev", lock, options=options)

    with pytest.raises(AnalysisInputError, match="not arm evidence"):
        _load(monkeypatch, [job], lock)
    loaded, _ = _load(monkeypatch, [job], lock, allow_dev_jobs=True)

    assert loaded.dev_job == DevRun(
        max_checkpoints=2, agent_override="noop", note=options.describe()
    )
    assert all(outcome.metrics["solve_rate"].status == "truncated" for outcome in loaded.outcomes)


def test_loading_needs_a_job_and_a_lock_naming_its_headline_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(AnalysisInputError, match="choose at least one job"):
        load_inputs(LoadRequest(job_dirs=(), store_root=Path("store"), registry=NO_REGISTRY))

    lock = analysis_lock(["correctness@10"])
    headless = {**lock, "assessment": {**lock["assessment"], "metric": None}}
    with pytest.raises(AnalysisInputError, match="names no headline metric"):
        _load(monkeypatch, [_toy_job("run-1", lock)], headless, lock_path=Path("pilot.lock.json"))


# ---- the analyze command ----------------------------------------------------------------

PINNED: dict[str, Any] = {
    "min_pairs": 6,
    "max_exclusion_fraction": 0.2,
    "max_exclusion_difference": 0.5,
    "alpha": 0.05,
    "min_effects": {"solve_rate": 0.25},
}


def _cli_lock(**pins: Any) -> dict[str, Any]:
    lock = analysis_lock(["correctness@10"], **pins)
    lock["analysis"] = {**lock["analysis"], "outcome_types": OUTCOME_TYPES}
    return lock


def _toy_inputs(**changes: Any) -> Any:
    """Six pairs whose solvable primary is unavailable (no solvable set), raw rate beside it."""
    outcomes = [
        outcome.model_copy(
            update={
                "metrics": {
                    **outcome.metrics,
                    SOLVE_RATE_SOLVABLE: Value.missing("unavailable", "no solvable set"),
                }
            }
        )
        for outcome in _outcomes()
    ]
    return dataclasses.replace(inputs(outcomes, 6), **changes)


def _analyze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lock: dict[str, Any],
    loaded: Any,
    *extra: str,
    output: Path | None = None,
) -> Any:
    job, store = tmp_path / "job", tmp_path / "store"
    job.mkdir(exist_ok=True)
    store.mkdir(exist_ok=True)
    monkeypatch.setattr(analyze_cli, "load_inputs", lambda request: (loaded, lock))
    return CliRunner().invoke(
        main,
        [
            "analyze",
            str(job),
            "--store",
            str(store),
            "--output",
            str(output or tmp_path / "out"),
            "--bootstrap-samples",
            "500",
            *extra,
        ],
    )


def _written(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "out" / "analysis.json").read_text())


def test_a_lock_that_pins_thresholds_labels_every_command_line_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pinned = _analyze(tmp_path, monkeypatch, _cli_lock(**PINNED), _toy_inputs())

    assert pinned.exit_code == 0, pinned.output
    settings = _written(tmp_path)["settings"]
    assert (settings["pinned_by_lock"], settings["overridden"]) == (True, [])
    assert (settings["min_pairs"], settings["min_effects"]) == (6, {"solve_rate": 0.25})
    assert "thresholds overridden" not in pinned.output
    assert "synthetic" in (tmp_path / "out" / "analysis.md").read_text()

    overridden = _analyze(
        tmp_path, monkeypatch, _cli_lock(**PINNED), _toy_inputs(), "--min-pairs", "5"
    )

    assert overridden.exit_code == 0, overridden.output
    assert _written(tmp_path)["settings"]["overridden"] == ["min_pairs"]
    assert "thresholds overridden: min_pairs" in overridden.output


def test_a_lock_without_pinned_thresholds_says_so_and_names_what_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _analyze(
        tmp_path,
        monkeypatch,
        _cli_lock(),
        _toy_inputs(),
        "--min-effect",
        "solve_rate=0.25",
        "--max-exclusion-difference",
        "0.5",
    )

    assert result.exit_code == 0, result.output
    report = _written(tmp_path)
    settings = report["settings"]
    assert settings["pinned_by_lock"] is False
    assert settings["overridden"] == ["max_exclusion_difference", "min_effect.solve_rate"]
    assert settings["changed_from_defaults"] == ["bootstrap_samples", "min_effect.solve_rate"]
    assert "does not pin verdict thresholds" in result.output
    assert "settings changed from defaults: bootstrap_samples" in result.output
    assert (report["dev_job"], "errata" in report, "solvable" in report) == (None, False, False)


def test_the_command_states_each_primary_verdict_with_the_raw_outcome_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _analyze(tmp_path, monkeypatch, _cli_lock(), _toy_inputs())

    assert result.exit_code == 0, result.output
    assert "guided vs baseline: primary solve_rate_solvable insufficient" in result.output
    assert "raw solve_rate beside it:" in result.output
    assert str(tmp_path / "out" / "analysis.json") in result.output


def test_development_jobs_and_pooling_findings_are_warned_and_not_citable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = _toy_inputs(
        dev_job=DevRun(max_checkpoints=2, agent_override=None, note="truncated to 2"),
        pooling=PoolingReport(
            unmeasured=("old-1",),
            overrides_used=("subject",),
            findings=(PoolFinding(field="subject", message="agent differs"),),
        ),
    )

    result = _analyze(tmp_path, monkeypatch, _cli_lock(), loaded)

    assert result.exit_code == 0, result.output
    assert "truncated to 2" in result.output
    assert "old-1" in result.output
    assert "subject: agent differs" in result.output
    assert _written(tmp_path)["citable"] is False


def test_the_command_refuses_unusable_requests_without_writing_a_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _cli_lock()
    for protected in ("job", "store"):
        (tmp_path / protected).mkdir(exist_ok=True)
        inside = _analyze(
            tmp_path, monkeypatch, lock, _toy_inputs(), output=tmp_path / protected / "analysis"
        )
        assert inside.exit_code != 0
        assert "--output must lie outside" in inside.output

    for bad in ("solve_rate", "no_such_outcome=1", "solve_rate=lots"):
        refused = _analyze(tmp_path, monkeypatch, lock, _toy_inputs(), "--min-effect", bad)
        assert refused.exit_code != 0, bad

    def unreadable(request: object) -> None:
        raise AnalysisInputError("job executed under another lock")

    monkeypatch.setattr(analyze_cli, "load_inputs", unreadable)
    failed = CliRunner().invoke(
        main,
        ["analyze", str(tmp_path / "job"), "--store", str(tmp_path / "store"), "--output", "o"],
    )
    assert failed.exit_code != 0
    assert "job executed under another lock" in failed.output

    base = _toy_inputs()
    planned = tuple(
        dataclasses.replace(item, attempt=item.attempt + 10) if item.arm != "baseline" else item
        for item in base.planned
    )
    unpaired = _analyze(tmp_path, monkeypatch, lock, dataclasses.replace(base, planned=planned))
    assert unpaired.exit_code != 0
    assert "no attempt is planned for both" in unpaired.output
    assert not (tmp_path / "out").exists()
