"""Scorer SDK contract (plan F1): manifests, statused results, output validation, resolver."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from raidar.scorers.builtin_manifests import BUILTIN_MANIFESTS, CODE_QUALITY, CORRECTNESS
from raidar.scorers.catalog import (
    ScorerRegistration,
    ScorerUnavailableError,
    scorer_resolver,
)
from raidar.scorers.sdk import result as result_module
from raidar.scorers.sdk.context import statused_result
from raidar.scorers.sdk.manifest import (
    EvidenceInput,
    MetricLevel,
    MetricSpec,
    MetricType,
    ScorerManifest,
)
from raidar.scorers.sdk.ownership import (
    DuplicateMetricError,
    MetricOwnershipError,
    MissingMetricError,
)
from raidar.scorers.sdk.result import (
    AssessResult,
    AssessStatus,
    Evidence,
    EvidenceSource,
    Observation,
    Span,
)
from raidar.scorers.sdk.validation import (
    InvalidEvidenceError,
    InvalidObservationError,
    validate_result,
    validate_scorer_set,
)
from raidar.scorers.trial_inputs import load_trial_context
from raidar.spec.loader import load_experiment
from raidar.spec.lock import build_lock, render_lock
from raidar.spec.metrics import MetricResolver
from raidar.spec.references import RefKind, RegistryRef, RegistryReferenceError, ResolverSet
from raidar.verifier.models import CaseStatus
from tests.experiment_spec_support import (
    PILOT_EXPERIMENT,
    PILOT_ONESHOT,
    experiment_document,
    fixture_resolvers,
    write_experiment,
)
from tests.scorer_support import (
    APP_V1,
    APP_V2,
    FakeScorer,
    StepFixture,
    fake_manifest,
    fake_scorer_v2,
    two_step_trial,
    write_files,
    write_trial,
)


@pytest.fixture
def ctx(tmp_path: Path):
    return load_trial_context(two_step_trial(tmp_path / "trial"))


def _with(result: AssessResult, **changes: object) -> AssessResult:
    return result.model_copy(update=changes)


def test_scorer_output_with_undeclared_duplicate_or_missing_metrics_is_rejected(ctx) -> None:
    manifest = fake_manifest()
    good = FakeScorer(manifest).assess(ctx)
    accepted = validate_result(manifest, ctx, good)
    assert len(accepted.observations) == len(good.observations)

    undeclared = Observation(
        metric="bogus", subject="checkpoint_1", status=AssessStatus.OK, value=1
    )
    with pytest.raises(MetricOwnershipError, match="unowned metric 'bogus'"):
        validate_result(manifest, ctx, _with(good, observations=(*good.observations, undeclared)))

    wrong_level = Observation(
        metric="solved_checkpoints", subject="checkpoint_1", status=AssessStatus.OK, value=1
    )
    with pytest.raises(MetricOwnershipError, match=r"solved_checkpoints\[checkpoint_1\]"):
        validate_result(manifest, ctx, _with(good, observations=(*good.observations, wrong_level)))

    with pytest.raises(DuplicateMetricError, match="Duplicate scorer metric output"):
        validate_result(
            manifest, ctx, _with(good, observations=(*good.observations, good.observations[0]))
        )

    with pytest.raises(MissingMetricError, match="did not emit all resolved metrics"):
        validate_result(manifest, ctx, _with(good, observations=good.observations[1:]))

    stray_evidence = Evidence(
        metric="bogus",
        subject="checkpoint_1",
        path="app/main.py",
        sha256="0" * 64,
        kind="finding",
        spans=(Span(line=3),),
    )
    with pytest.raises(MetricOwnershipError):
        validate_result(manifest, ctx, _with(good, evidence=(stray_evidence,)))


def test_a_newer_manifest_rejects_output_missing_its_new_metric(ctx) -> None:
    v1_output = FakeScorer(fake_manifest("1")).assess(ctx)
    with pytest.raises(MissingMetricError, match=r"test_files\[checkpoint_1\]"):
        validate_result(fake_scorer_v2().manifest, ctx, v1_output)


def test_unavailable_is_never_a_value_and_ok_values_fit_their_type(ctx) -> None:
    with pytest.raises(ValidationError, match="never zero"):
        Observation(
            metric="solved", subject="c", status=AssessStatus.UNAVAILABLE, value=0.0, reason="x"
        )
    with pytest.raises(ValidationError, match="needs a reason"):
        Observation(metric="solved", subject="c", status=AssessStatus.NOT_APPLICABLE)
    with pytest.raises(ValidationError, match="needs a value"):
        Observation(metric="solved", subject="c", status=AssessStatus.OK)
    with pytest.raises(ValidationError, match="needs a reason"):
        AssessResult(status=AssessStatus.INELIGIBLE, observations=())

    manifest = fake_manifest()
    good = FakeScorer(manifest).assess(ctx)
    for metric, value, problem in (
        ("solved", 2.0, "outside"),
        ("production_files", 1.5, "integral"),
        ("production_files", -1.0, "outside"),
    ):
        observations = tuple(
            item.model_copy(update={"value": value})
            if (item.metric, item.subject) == (metric, "checkpoint_1")
            else item
            for item in good.observations
        )
        with pytest.raises(InvalidObservationError, match=problem):
            validate_result(manifest, ctx, _with(good, observations=observations))


def test_result_status_and_ratio_parts_must_agree_with_the_values(ctx) -> None:
    manifest = fake_manifest()
    good = FakeScorer(manifest).assess(ctx)
    with pytest.raises(ValidationError, match="cannot carry ok observations"):
        AssessResult.model_validate({**good.model_dump(), "status": "unavailable", "reason": "x"})
    unmeasured = statused_result(manifest, ctx, AssessStatus.UNAVAILABLE, "no snapshots")
    with pytest.raises(ValidationError, match="at least one ok observation"):
        AssessResult.model_validate({**unmeasured.model_dump(), "status": "ok"})

    Observation(
        metric="m", subject="c", status=AssessStatus.OK, value=0.5, numerator=1, denominator=2
    )
    for parts, problem in (
        ({"numerator": 1, "denominator": 0}, "zero denominator"),
        ({"numerator": 1, "denominator": 3}, "not numerator/denominator"),
        ({"numerator": 1}, "reported together"),
    ):
        with pytest.raises(ValidationError, match=problem):
            Observation(metric="m", subject="c", status=AssessStatus.OK, value=0.9, **parts)


def test_evidence_must_point_at_real_lines_of_the_subjects_stored_files(ctx) -> None:
    manifest = fake_manifest()
    good = FakeScorer(manifest).assess(ctx)
    real = next(e for e in good.evidence if e.subject == "checkpoint_2")
    assert validate_result(manifest, ctx, _with(good, evidence=(real,))).evidence == (real,)
    trace_file = ctx.step("checkpoint_1").trace_dir / "trajectory.json"
    trace = Evidence(
        metric="solved",
        subject="checkpoint_1",
        source=EvidenceSource.TRACE,
        path="trajectory.json",
        sha256=hashlib.sha256(trace_file.read_bytes()).hexdigest(),
        kind="trace",
        spans=(Span(line=1),),
    )
    validate_result(manifest, ctx, _with(good, evidence=(trace,)))
    # checkpoint_2's file is not evidence for checkpoint_1; a transition may cite either side.
    moved = real.model_copy(update={"subject": "checkpoint_1"})
    transition = real.model_copy(
        update={"metric": "growth", "subject": "checkpoint_1->checkpoint_2"}
    )
    validate_result(manifest, ctx, _with(good, evidence=(transition,)))
    for fabricated, problem in (
        (real.model_copy(update={"path": "does/not/exist.py"}), "no such file"),
        (real.model_copy(update={"path": "../trial.py"}), "relative"),
        (real.model_copy(update={"sha256": "0" * 64}), "sha256"),
        (real.model_copy(update={"spans": (Span(line=99999),)}), "past the end"),
        (real.model_copy(update={"spans": (Span(line=1, end_line=99),)}), "past the end"),
        (moved, "sha256"),
    ):
        with pytest.raises(InvalidEvidenceError, match=problem):
            validate_result(manifest, ctx, _with(good, evidence=(fabricated,)))


def test_snapshot_scorers_must_report_steps_with_unreadable_evidence_as_ineligible(
    tmp_path: Path,
) -> None:
    latin1 = {**APP_V2, "app/core.py": "s = 'caf\xe9'\n".encode("latin-1")}
    trial = write_trial(
        tmp_path / "trial",
        [
            StepFixture("checkpoint_1", APP_V1, (CaseStatus.PASSED,)),
            StepFixture("checkpoint_2", latin1, (CaseStatus.PASSED,)),
        ],
    )
    ctx = load_trial_context(trial)
    manifest = fake_manifest()
    result = validate_result(manifest, ctx, FakeScorer(manifest).assess(ctx))
    statuses = {(o.metric, o.subject): o.status for o in result.observations}
    assert statuses[("production_files", "checkpoint_2")] is AssessStatus.INELIGIBLE
    assert statuses[("growth", "checkpoint_1->checkpoint_2")] is AssessStatus.INELIGIBLE
    assert statuses[("production_files", "checkpoint_1")] is AssessStatus.OK

    def forced(metric: str, subject: str) -> AssessResult:
        return _with(
            result,
            observations=tuple(
                o.model_copy(update={"status": AssessStatus.OK, "value": 1.0, "reason": None})
                if (o.metric, o.subject) == (metric, subject)
                else o
                for o in result.observations
            ),
        )

    for metric, subject in (
        ("production_files", "checkpoint_2"),
        ("growth", "checkpoint_1->checkpoint_2"),
    ):
        with pytest.raises(InvalidObservationError, match="report it as ineligible"):
            validate_result(manifest, ctx, forced(metric, subject))
    # A scorer that does not read snapshots is not bound by the snapshot inventory.
    oracle_only = manifest.model_copy(update={"evidence_required": (EvidenceInput.ORACLE_CASES,)})
    validate_result(oracle_only, ctx, forced("production_files", "checkpoint_2"))


def test_scorers_never_see_arm_or_model_labels_in_structured_inputs(ctx) -> None:
    for step in ctx.steps:
        assert step.agent_result is not None
        assert "arm-model-label" not in json.dumps(step.agent_result)
        assert step.agent_result["model_usage"] == [
            {"n_input_tokens": 7, "n_cache_tokens": None, "n_output_tokens": None, "cost_usd": None}
        ]


def test_implementation_digest_covers_helpers_the_scorer_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "plugscorer"
    write_files(
        package,
        {
            "__init__.py": "",
            "impl.py": "from .kernels import weight\nfrom plugscorer import shared\n",
            "kernels.py": "def weight():\n    return 1\n",
            "shared.py": "LIMIT = 3\n",
            "unused.py": "X = 0\n",
        },
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    registration = ScorerRegistration(fake_manifest(), "plugscorer.impl:factory")
    before = registration.implementation_digest()
    (package / "unused.py").write_text("X = 1\n")
    assert registration.implementation_digest() == before
    # Docstring, comment and layout edits to a covered module keep every stored Assessment.
    (package / "kernels.py").write_text(
        '"""Kernel helpers."""\n\n\ndef weight():  # the weight\n'
        '    """Always one."""\n\n    return (\n        1\n    )\n'
    )
    (package / "__init__.py").write_text('"""The plug-in scorer package."""\n')
    assert registration.implementation_digest() == before
    (package / "kernels.py").write_text("def weight():\n    return 2\n")
    after_kernel = registration.implementation_digest()
    assert after_kernel != before
    (package / "shared.py").write_text("LIMIT = 4\n")
    assert registration.implementation_digest() != after_kernel
    # Raidar modules the scorer imports are covered too.
    covered = ScorerRegistration(fake_manifest(), "tests.scorer_support:fake_scorer_v1")
    sdk_result = Path(result_module.__file__)
    original = sdk_result.read_bytes()
    baseline = covered.implementation_digest()
    try:
        sdk_result.write_bytes(original + b"\n_TOUCHED = 1\n")
        assert covered.implementation_digest() != baseline
    finally:
        sdk_result.write_bytes(original)


def test_statused_result_explicitly_covers_every_declared_metric_without_values(ctx) -> None:
    unsupported = statused_result(
        CODE_QUALITY, ctx, AssessStatus.UNSUPPORTED, "runtime go has no adapter"
    )
    accepted = validate_result(CODE_QUALITY, ctx, unsupported)
    assert accepted.status is AssessStatus.UNSUPPORTED
    assert {item.value for item in accepted.observations} == {None}
    transitions = [o for o in accepted.observations if o.metric == "rework"]
    assert [o.subject for o in transitions] == ["checkpoint_1->checkpoint_2"]
    with pytest.raises(ValueError, match="non-ok"):
        statused_result(CODE_QUALITY, ctx, AssessStatus.OK, "fine")


def test_accepted_results_are_canonical_whatever_the_emission_order(ctx) -> None:
    manifest = fake_manifest()
    emitted = FakeScorer(manifest).assess(ctx)
    shuffled = _with(
        emitted,
        observations=tuple(sorted(emitted.observations, key=lambda o: (o.subject, o.metric))),
        evidence=tuple(reversed(emitted.evidence)),
    )
    first = validate_result(manifest, ctx, emitted)
    assert validate_result(manifest, ctx, shuffled) == first
    assert [o.metric for o in first.observations[:4]] == ["production_files"] * 2 + ["solved"] * 2


def test_manifests_reject_ambiguous_declarations() -> None:
    base = fake_manifest().model_dump()
    duplicate = {**base, "metrics": [*base["metrics"], base["metrics"][0]]}
    with pytest.raises(ValidationError, match="duplicate metric"):
        ScorerManifest.model_validate(duplicate)
    with pytest.raises(ValidationError, match="runtime-neutral"):
        ScorerManifest.model_validate({**base, "tools": ["scb-check"]})
    with pytest.raises(ValidationError, match=r"\[0, 1\]"):
        MetricSpec.model_validate(
            {**base["metrics"][0], "type": MetricType.RATIO, "range": (0.0, 100.0)}
        )


def test_scorers_in_one_plan_have_disjoint_metric_namespaces() -> None:
    validate_scorer_set(BUILTIN_MANIFESTS)
    with pytest.raises(DuplicateMetricError, match="fake-quality.production_files"):
        validate_scorer_set([fake_manifest("1"), fake_manifest("2")])


def test_builtin_manifests_declare_levels_the_policies_need() -> None:
    levels = {metric.id: metric.level for metric in CODE_QUALITY.metrics}
    assert levels["rework"] is MetricLevel.TRANSITION
    assert levels["regression"] is MetricLevel.TRANSITION
    assert levels["verbosity"] is MetricLevel.CHECKPOINT
    assert CORRECTNESS.metric("reached") is not None
    assert all(metric.tier.value == "deterministic" for metric in CORRECTNESS.metrics)


def _pilot_scorer_refs() -> list[str]:
    refs: list[str] = []
    for path in (PILOT_EXPERIMENT, PILOT_ONESHOT):
        refs += yaml.safe_load(path.read_text())["assessment"]["scorers"]
    return sorted(set(refs))


def test_pilot_scorer_references_resolve_through_the_entry_point(tmp_path: Path) -> None:
    resolver = ResolverSet().resolver_for(RefKind.SCORER)
    assert resolver is not None
    refs = [*_pilot_scorer_refs(), "process-compliance@1"]
    resolved = {
        ref: resolver.resolve(RegistryRef.parse(RefKind.SCORER, ref), base_dir=tmp_path)
        for ref in refs
    }
    assert set(resolved) >= {"correctness@1", "code-quality@1", "resource-usage@1"}
    quality = resolved["code-quality@1"]
    assert quality.tool_versions["ruff"] == "0.8.6"
    assert quality.tool_versions["scb-check"] == "0.1.3"
    assert quality.metadata["manifest"] == CODE_QUALITY.definition()
    assert resolved["correctness@1"].tool_versions == {}
    again = resolver.resolve(RegistryRef.parse(RefKind.SCORER, "code-quality@1"), base_dir=tmp_path)
    assert again.digest == quality.digest
    with pytest.raises(RegistryReferenceError, match="unknown scorer 'nope@1'"):
        resolver.resolve(RegistryRef.parse(RefKind.SCORER, "nope@1"), base_dir=tmp_path)


def test_scorers_without_an_implementation_cannot_assess() -> None:
    registration = ScorerRegistration(CORRECTNESS)
    with pytest.raises(ScorerUnavailableError, match="no implementation"):
        registration.load()


def test_lock_records_scorer_manifests_and_tool_pins_byte_identically(tmp_path: Path) -> None:
    scorers = _pilot_scorer_refs()
    path = write_experiment(tmp_path, experiment_document(assessment={"scorers": scorers}))
    problem, component, _ = fixture_resolvers()
    resolvers = ResolverSet(
        [problem, component, MetricResolver(), scorer_resolver()], entry_point_group=None
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
