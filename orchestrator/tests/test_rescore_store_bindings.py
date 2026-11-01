"""`raidar assess --rescore` binds store-aware scorers, raw payloads and
problem-derived config (plan F2, G2, G5, H2)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click
import pytest
import yaml
from click.testing import CliRunner, Result

from raidar.cli import main
from raidar.cli.assess import (
    _check_as1,
    _job_lock,
    _narrowed,
    _RescoreRequest,
    _settings,
    as1_issues,
    run_assessment_config,
    run_lock,
)
from raidar.contract.runtimes import ANY_RUNTIME
from raidar.engine.job.assessment import ASSESSMENT_CONFIG_FILE
from raidar.evaluation.analysis.sources import AnalysisInputError
from raidar.grading import offline as offline_module
from raidar.grading.bundle import BundleError
from raidar.grading.models import CaseStatus
from raidar.grading.offline import (
    OFFLINE_COVERAGE_SCORERS,
    PRE_FIX_GRADE_SCORERS,
    OfflineGradingError,
)
from raidar.grading.regrade import RegradeOptions
from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.framework.context import AssessContext, statused_result
from raidar.scoring.framework.registry import (
    ScorerRegistration,
    ScorerRegistry,
    ScorerUnavailableError,
)
from raidar.scoring.framework.rescore import (
    AssessmentRecord,
    RescoreError,
    RescoreOutcome,
    qualified_trial_id,
    review_trial_id,
)
from raidar.scoring.framework.rescore_config import RescoreConfig, load_rescore_config
from raidar.scoring.framework.result import AssessResult, AssessStatus
from raidar.scoring.framework.store import AssessmentConflictError, AssessmentStore
from tests.scorer_support import (
    APP_V1,
    FakeScorer,
    StepFixture,
    assess_fixed,
    fake_manifest,
    snapshot_context,
    two_step_context,
)

FAKE = "fake-quality@1"


class Awaiting(FakeScorer):
    """A fake scorer whose external decision is answered only once ``answered`` is set."""

    answered = False
    runs = 0

    def awaiting_answers(self) -> bool:
        return not type(self).answered

    def assess(self, ctx: AssessContext) -> AssessResult:
        type(self).runs += 1
        reason = "answered" if type(self).answered else "awaiting"
        return statused_result(self.manifest, ctx, AssessStatus.UNAVAILABLE, reason)


def awaiting_scorer() -> Awaiting:
    return Awaiting(fake_manifest("1"))


def test_a_provisional_assessment_is_rerun_and_superseded_once_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Awaiting, "answered", False)
    monkeypatch.setattr(Awaiting, "runs", 0)
    ctx = two_step_context(tmp_path / "trial")
    store = AssessmentStore(tmp_path / "store")
    registry = _registry(implementation="tests.test_rescore_store_bindings:awaiting_scorer")

    first = _assess(store, registry, ctx)
    provisional = store.path_for(first.key).read_bytes()
    assert first.outcome is RescoreOutcome.ADDED
    assert b'"provisional": true' in provisional

    # Still unanswered: rerun (never reused), same bytes, nothing replaced.
    again = _assess(store, registry, ctx)
    assert (again.outcome, Awaiting.runs) == (RescoreOutcome.EXISTING, 2)
    assert store.path_for(first.key).read_bytes() == provisional

    monkeypatch.setattr(Awaiting, "answered", True)
    answered = _assess(store, registry, ctx)
    assert answered.outcome is RescoreOutcome.SUPERSEDED
    assert answered.key == first.key, "analysis finds the complete Assessment under the same key"
    complete = store.get(first.key)
    assert complete is not None
    assert complete.provisional is False
    assert complete.result.reason == "answered"
    assert store.superseded_path(first.key, provisional).read_bytes() == provisional

    # A complete Assessment is reused without running, and never replaced.
    reused = _assess(store, registry, ctx)
    assert (reused.outcome, Awaiting.runs) == (RescoreOutcome.EXISTING, 3)
    monkeypatch.setattr(Awaiting, "answered", False)
    with pytest.raises(AssessmentConflictError, match="different bytes"):
        _assess(store, registry, ctx, replay=True)
    assert store.get(first.key) == complete


class RawScorer(FakeScorer):
    """A fake scorer that also returns raw tool output."""

    raw: Mapping[str, bytes] = {"ruff.json": b'{"findings": []}\n'}

    def assess_with_raw(self, ctx: AssessContext) -> tuple[AssessResult, Mapping[str, bytes]]:
        return self.assess(ctx), dict(self.raw)


def raw_scorer() -> RawScorer:
    return RawScorer(fake_manifest("1"))


def _registry(**registration: object) -> ScorerRegistry:
    return ScorerRegistry([ScorerRegistration(fake_manifest("1"), **registration)])  # type: ignore[arg-type]


def _assess(
    store: AssessmentStore, registry: ScorerRegistry, ctx: AssessContext, **kwargs: bool
) -> AssessmentRecord:
    return assess_fixed(store, registry.get(FAKE), ctx, trial="trial", **kwargs)


def test_raw_payloads_are_stored_content_addressed_and_proven_on_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = two_step_context(tmp_path / "trial")
    store = AssessmentStore(tmp_path / "store")
    registry = _registry(implementation="tests.test_rescore_store_bindings:raw_scorer")

    added = _assess(store, registry, ctx)
    assessment = store.get(added.key)
    assert assessment is not None
    digest = assessment.raw_payloads["ruff.json"]
    assert store.get_raw(digest) == RawScorer.raw["ruff.json"]

    replayed = _assess(store, registry, ctx, replay=True)
    assert replayed.outcome is RescoreOutcome.REPLAYED_IDENTICAL

    monkeypatch.setattr(RawScorer, "raw", {"ruff.json": b'{"findings": [1]}\n'})
    with pytest.raises(AssessmentConflictError, match="different bytes"):
        _assess(store, registry, ctx, replay=True)
    assert store.get_raw(digest) == b'{"findings": []}\n', "stored raw bytes are never replaced"

    plain = AssessmentStore(tmp_path / "plain")
    without = _assess(plain, _registry(implementation=_FAKE_V1), ctx)
    assert b"raw_payloads" not in plain.path_for(without.key).read_bytes()


_FAKE_V1 = "tests.scorer_support:fake_scorer_v1"


class ConfigEcho(FakeScorer):
    """Reports the config it was run with as every observation's reason."""

    def assess(self, ctx: AssessContext) -> AssessResult:
        reason = json.dumps(dict(ctx.config), sort_keys=True)
        return statused_result(self.manifest, ctx, AssessStatus.UNAVAILABLE, reason)


def config_echo() -> ConfigEcho:
    return ConfigEcho(fake_manifest("1"))


def _seen_config(store: AssessmentStore, key: str) -> dict[str, object]:
    assessment = store.get(key)
    assert assessment is not None
    return json.loads(assessment.result.observations[0].reason or "{}")


def test_problem_facts_default_the_scorer_config_and_explicit_config_wins(
    tmp_path: Path,
) -> None:
    steps = [StepFixture("checkpoint_1", APP_V1, (CaseStatus.PASSED,))]
    ctx = snapshot_context(tmp_path / "trial", steps)
    with_entry = replace(ctx, problem_config={"entry_file": "app/main.py"})
    registry = _registry(
        implementation="tests.test_rescore_store_bindings:config_echo",
        problem_config=("entry_file",),
    )
    store = AssessmentStore(tmp_path / "store")

    default = _assess(store, registry, with_entry)
    assert _seen_config(store, default.key) == {"entry_file": "app/main.py"}
    stored = store.get(default.key)
    assert stored is not None
    assert stored.config == {}, "the key hashes explicit config; the default is an input"

    explicit = replace(with_entry, config={"entry_file": "other.py", "strict": True})
    overridden = _assess(store, registry, explicit)
    assert _seen_config(store, overridden.key) == {"entry_file": "other.py", "strict": True}
    assert overridden.key != default.key

    # A scorer that does not declare the key never sees the problem's value.
    undeclared = _registry(implementation="tests.test_rescore_store_bindings:config_echo")
    other = AssessmentStore(tmp_path / "s2")
    assert _seen_config(other, _assess(other, undeclared, with_entry).key) == {}

    # Problems without an entrypoint pass nothing, so the scorer's own default applies.
    bare = AssessmentStore(tmp_path / "s3")
    assert _seen_config(bare, _assess(bare, registry, ctx).key) == {}


def test_a_store_aware_scorer_requires_an_implementation() -> None:
    with pytest.raises(ValueError, match="needs an implementation"):
        ScorerRegistration(fake_manifest("1"), store_implementation="a:b")


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        ({"experiment": "pilot", "job_id": "run-a"}, "pilot.run-a.t1"),
        ({"experiment": "pilot"}, "pilot.jobdir.t1"),  # no job id: the job directory's name
        ({"job_id": "run-a"}, "run-a/t1"),  # no experiment: <job>/<trial>
        ({}, "jobdir/t1"),
        ({"experiment": "a b/c", "job_id": "run:a"}, "a-b-c.run-a.t1"),  # unsafe characters
    ],
)
def test_a_trial_in_a_job_is_recorded_under_its_review_id(
    tmp_path: Path, manifest: dict[str, str], expected: str
) -> None:
    assert review_trial_id(manifest, "jobdir", "t1") == expected
    job = tmp_path / "jobdir"
    job.mkdir()
    (job / "job.json").write_text(json.dumps(manifest))
    assert qualified_trial_id(job / "t1") == expected


def test_the_runs_assessment_config_is_found_from_a_trial_dir_or_its_job_dir(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    trial = run / "trials" / "trial"
    trial.mkdir(parents=True)
    config = run / ASSESSMENT_CONFIG_FILE
    config.write_text("scorers: {}\n")
    for target in (trial, trial.parent, run):
        assert run_assessment_config(target) == config.resolve()
    loose = tmp_path / "loose" / "trials" / "trial"
    loose.mkdir(parents=True)
    assert run_assessment_config(loose) is None


def test_a_runs_config_is_narrowed_to_the_scorers_being_run_not_refused() -> None:
    config = RescoreConfig(scorers={"a@1": {"x": 1}, "b@1": {"y": 2}})
    narrowed = _narrowed(config, ("a@1", "c@1"))
    assert narrowed.scorers == {"a@1": {"x": 1}}
    assert narrowed.inventory_policy == config.inventory_policy


@pytest.fixture
def steps_taken(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Offline grading, coverage and the rescore itself recorded in the order they happen."""
    steps: list[str] = []

    async def grade(*_: object) -> dict[str, object]:
        steps.append("pre_fix")
        return {"kind": "pre-fix", "errors": 0}

    async def cover(*_: object) -> dict[str, object]:
        steps.append("coverage")
        return {"kind": "coverage", "errors": 0}

    def rescore(*_: object, **__: object) -> list[object]:
        steps.append("rescore")
        return []

    monkeypatch.setattr(offline_module, "grade_pre_fix_path", grade)
    monkeypatch.setattr(offline_module, "produce_coverage_path", cover)
    options = RegradeOptions(docker_context="test-ctx")
    monkeypatch.setattr("raidar.cli.assess.grading_options", lambda *_: options)
    monkeypatch.setattr("raidar.cli.assess._check_as1", lambda *_: None)
    monkeypatch.setattr("raidar.cli.assess.rescore_path", rescore)
    return steps


def _rescore_cli(tmp_path: Path, refs: tuple[str, ...], *extra: str) -> Result:
    target = tmp_path / "job"
    target.mkdir(exist_ok=True)
    args = ["assess", "--rescore", str(target), "--store", str(tmp_path / "store")]
    for ref in refs:
        args += ["--scorer", ref]
    return CliRunner().invoke(main, [*args, *extra])


PRE_FIX_SCORER = next(iter(PRE_FIX_GRADE_SCORERS))
COVERAGE_SCORER = next(iter(OFFLINE_COVERAGE_SCORERS))


@pytest.mark.parametrize(
    ("refs", "replay", "expected"),
    [
        ((PRE_FIX_SCORER,), False, ["pre_fix", "rescore"]),
        ((COVERAGE_SCORER,), False, ["coverage", "rescore"]),
        ((PRE_FIX_SCORER, COVERAGE_SCORER), False, ["pre_fix", "coverage", "rescore"]),
        (("correctness@10",), False, ["rescore"]),
        ((PRE_FIX_SCORER, COVERAGE_SCORER), True, ["rescore"]),
    ],
)
def test_a_rescore_first_makes_only_the_grades_its_scorers_read_and_never_on_replay(
    tmp_path: Path,
    steps_taken: list[str],
    refs: tuple[str, ...],
    replay: bool,
    expected: list[str],
) -> None:
    result = _rescore_cli(tmp_path, refs, *(["--replay"] if replay else []))
    assert result.exit_code == 0, result.output
    assert steps_taken == expected
    report = json.loads(result.output)
    assert ("pre_fix_grading" in report) is ("pre_fix" in expected)
    assert ("offline_coverage" in report) is ("coverage" in expected)


def test_a_grading_run_that_cannot_proceed_stops_the_rescore(
    tmp_path: Path, steps_taken: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refused(*_: object) -> dict[str, object]:
        raise OfflineGradingError("target is not trusted")

    monkeypatch.setattr(offline_module, "grade_pre_fix_path", refused)
    result = _rescore_cli(tmp_path, (PRE_FIX_SCORER,))
    assert result.exit_code == 1
    assert "not trusted" in result.output
    assert steps_taken == []


@pytest.mark.parametrize(
    "error",
    [
        RescoreError("bad input"),
        ScorerUnavailableError("no implementation"),
        AssessmentConflictError("different bytes"),
    ],
)
def test_a_rescore_the_framework_refuses_is_a_command_error_without_a_report(
    tmp_path: Path, steps_taken: list[str], monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def refuse(*_: object, **__: object) -> list[object]:
        raise error

    monkeypatch.setattr("raidar.cli.assess.rescore_path", refuse)
    result = _rescore_cli(tmp_path, ("correctness@10",))
    assert result.exit_code == 1
    assert str(error) in result.output
    assert "assessments" not in result.output


def test_a_rescore_needs_a_store_outside_the_job_and_one_mode(tmp_path: Path) -> None:
    job = tmp_path / "job"
    job.mkdir()
    runner = CliRunner()
    base = ["assess", "--rescore", str(job), "--scorer", "correctness@10"]
    no_store = runner.invoke(main, base)
    assert (no_store.exit_code, "--store" in no_store.output) == (2, True)
    inside = runner.invoke(main, [*base, "--store", str(job / "store")])
    assert (inside.exit_code, "must not be inside" in inside.output) == (2, True)
    both = runner.invoke(main, [*base, "--regrade", str(job)])
    assert both.exit_code == 2
    neither = runner.invoke(main, ["assess"])
    assert neither.exit_code == 2
    misplaced = runner.invoke(main, [*base, "--store", str(tmp_path / "s"), "--coverage-check"])
    assert (misplaced.exit_code, "--regrade" in misplaced.output) == (2, True)


def test_a_scorer_that_cannot_read_the_jobs_runtime_is_refused_by_the_assess_gate() -> None:
    registry = default_scorer_registry()
    refs = registry.refs()
    neutral = {ref for ref in refs if ANY_RUNTIME in registry.get(ref).manifest.runtimes}
    bound = set(refs) - neutral
    assert neutral
    assert bound
    assert as1_issues(None, tuple(neutral), None) == []
    for ref in bound:
        (issue,) = as1_issues(None, (ref,), None)
        assert "[AS1]" in issue
        assert ref in issue
        runtime = registry.get(ref).manifest.runtimes[0]
        assert as1_issues({"spec": {"runtime": runtime}}, (ref,), None) == []


def test_the_assessment_settings_come_from_config_then_the_runs_file_narrowed_to_the_scorers(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    trial = run / "trials" / "trial"
    trial.mkdir(parents=True)
    run_file = run / ASSESSMENT_CONFIG_FILE
    run_file.write_text(yaml.safe_dump({"scorers": {"a@1": {"x": 1}, "b@1": {"y": 2}}}))
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(yaml.safe_dump({"scorers": {"c@1": {"z": 3}}}))

    def request(target: Path, config: Path | None) -> _RescoreRequest:
        return _RescoreRequest(target, ("a@1", "c@1"), None, config, None, False)

    given, given_path = _settings(request(trial, explicit))
    assert (given.scorers, given_path) == ({"c@1": {"z": 3}}, explicit)
    found, found_path = _settings(request(trial, None))
    assert (found.scorers, found_path) == ({"a@1": {"x": 1}}, run_file.resolve())
    none, none_path = _settings(request(tmp_path / "loose", None))
    assert (none.scorers, none_path) == ({}, None)


def _lock_with_problem(digest: str) -> dict[str, Any]:
    problems = {"toy@1": {"metadata": {"bundle_sha256": digest}}}
    return {"spec": {"runtime": "python"}, "execution": {"references": {"problem": problems}}}


def test_a_bundle_override_must_be_the_bundle_of_a_problem_the_lock_binds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle: Any = SimpleNamespace(digest=lambda: "d1" + "0" * 62)
    monkeypatch.setattr("raidar.cli.assess.load_bundle", lambda path: bundle)
    lock = _lock_with_problem("d1" + "0" * 62)
    assert as1_issues(lock, (), tmp_path) == []
    (mismatch,) = as1_issues(_lock_with_problem("e" * 64), (), tmp_path)
    assert "[AS1]" in mismatch
    assert "toy@1" in mismatch

    def broken(path: Path) -> Any:
        raise BundleError("not a bundle")

    monkeypatch.setattr("raidar.cli.assess.load_bundle", broken)
    (unusable,) = as1_issues(lock, (), tmp_path)
    assert "[AS1]" in unusable
    assert "not a bundle" in unusable
    # Without a lock there is no binding to compare the bundle with.
    assert as1_issues(None, (), tmp_path) == []


def test_an_unknown_scorer_is_left_to_the_rescore_to_report_not_the_gate() -> None:
    assert as1_issues(None, ("nope@1",), None) == []


def test_the_assess_gate_refuses_the_run_listing_every_issue(tmp_path: Path) -> None:
    _check_as1(tmp_path, None, ("correctness@10",), None)
    with pytest.raises(click.ClickException, match="assess refused") as refused:
        _check_as1(tmp_path, None, (COVERAGE_SCORER,), None)
    assert COVERAGE_SCORER in refused.value.message


def test_the_job_lock_must_exist_and_be_the_one_the_job_ran_under(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job = tmp_path / "job"
    job.mkdir()
    lock = tmp_path / "experiment.lock.json"
    monkeypatch.setattr(
        "raidar.cli.assess.find_job_manifest", lambda target: (job, {"lock_sha256": "l" * 64})
    )
    monkeypatch.setattr("raidar.cli.assess.default_lock", lambda found: lock)
    monkeypatch.setattr("raidar.cli.assess.read_lock", lambda path: {"lock_sha256": "x" * 64})

    with pytest.raises(ValueError, match="does not exist"):
        run_lock(job, None)
    lock.write_text("{}")
    with pytest.raises(ValueError, match="not the lock job"):
        run_lock(job, None)
    monkeypatch.setattr("raidar.cli.assess.read_lock", lambda path: {"lock_sha256": "l" * 64})
    assert run_lock(job, None) == lock
    assert _job_lock(job, None) == {"lock_sha256": "l" * 64}
    with pytest.raises(click.ClickException, match="does not exist"):
        _job_lock(job, tmp_path / "missing.lock.json")

    monkeypatch.setattr("raidar.cli.assess.find_job_manifest", lambda target: None)
    assert run_lock(tmp_path, None) is None
    assert run_lock(tmp_path, lock) == lock
    assert _job_lock(tmp_path, None) is None


def test_a_jobs_lock_supplies_the_settings_narrowed_to_the_scorers_being_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock = tmp_path / "experiment.lock.json"
    monkeypatch.setattr("raidar.cli.assess.run_lock", lambda target, explicit: lock)
    monkeypatch.setattr("raidar.cli.assess.read_lock", lambda path: {})
    locked = RescoreConfig(scorers={"a@1": {"x": 1}, "b@1": {"y": 2}})
    monkeypatch.setattr("raidar.cli.assess.lock_rescore_config", lambda document: locked)

    settings, path = _settings(_RescoreRequest(tmp_path, ("a@1",), None, None, None, False))

    assert (settings.scorers, path) == ({"a@1": {"x": 1}}, lock)

    def refused(target: Path, explicit: Path | None) -> Path:
        raise AnalysisInputError("two jobs match")

    monkeypatch.setattr("raidar.cli.assess.run_lock", refused)
    with pytest.raises(ValueError, match="two jobs match"):
        _settings(_RescoreRequest(tmp_path, ("a@1",), None, None, None, False))


@pytest.mark.parametrize(
    ("extra", "match", "ran", "exit_code"),
    [
        ([], True, "regrade", 0),
        ([], False, "regrade", 1),
        (["--coverage-check"], True, "coverage_check", 0),
    ],
)
def test_a_regrade_reports_and_fails_on_any_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    extra: list[str],
    match: bool,
    ran: str,
    exit_code: int,
) -> None:
    ran_names: list[str] = []

    def report(name: str) -> Any:
        def run(*_: object) -> dict[str, object]:
            ran_names.append(name)
            return {"match": match}

        return run

    monkeypatch.setattr("raidar.cli.assess.regrade", report("regrade"))
    monkeypatch.setattr("raidar.cli.assess.check_coverage", report("coverage_check"))
    monkeypatch.setattr("raidar.cli.assess._check_as1", lambda *_: None)
    options = RegradeOptions(docker_context="test-ctx")
    monkeypatch.setattr("raidar.cli.assess.grading_options", lambda *_: options)
    job = tmp_path / "job"
    job.mkdir()
    out = tmp_path / "report.json"

    result = CliRunner().invoke(main, ["assess", "--regrade", str(job), "--out", str(out), *extra])

    assert result.exit_code == exit_code, result.output
    assert ran_names == [ran]
    assert json.loads(out.read_text()) == {"match": match}


BOUND_ROOTS: list[Path] = []


def store_bound_scorer(root: Path) -> FakeScorer:
    BOUND_ROOTS.append(root)
    return FakeScorer(fake_manifest("1"))


def store_bound_other_scorer(root: Path) -> FakeScorer:
    return FakeScorer(fake_manifest("2"))


def test_a_store_bound_scorer_is_built_for_the_store_and_must_be_the_registered_one(
    tmp_path: Path,
) -> None:
    bound = "tests.test_rescore_store_bindings:store_bound_scorer"
    registration = ScorerRegistration(
        fake_manifest("1"), implementation=_FAKE_V1, store_implementation=bound
    )
    assert registration.load_for_store(tmp_path).manifest == registration.manifest
    assert BOUND_ROOTS[-1] == tmp_path
    wrong = ScorerRegistration(
        fake_manifest("1"),
        implementation=_FAKE_V1,
        store_implementation="tests.test_rescore_store_bindings:store_bound_other_scorer",
    )
    with pytest.raises(ScorerUnavailableError, match="does not implement"):
        wrong.load_for_store(tmp_path)
    plain = ScorerRegistration(fake_manifest("1"), implementation=_FAKE_V1)
    assert plain.load_for_store(tmp_path).manifest == plain.manifest


@pytest.mark.parametrize(
    "text",
    ["inventory_policy: [1]\n", "scorers: {a@1: not-a-mapping}\n", "{unclosed: [\n"],
)
def test_an_unreadable_or_invalid_rescore_config_is_a_rescore_error(
    tmp_path: Path, text: str
) -> None:
    path = tmp_path / "assess.yaml"
    path.write_text(text)
    with pytest.raises(RescoreError, match="invalid rescore config"):
        load_rescore_config(path)
    with pytest.raises(RescoreError, match="invalid rescore config"):
        load_rescore_config(tmp_path / "missing.yaml")
    path.write_text("scorers:\n  a@1: {x: 1}\n")
    assert load_rescore_config(path).scorers == {"a@1": {"x": 1}}
