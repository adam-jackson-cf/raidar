"""Scorer SDK contract (plan F1): manifests, statused results, output validation, resolver."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from raidar.contract.references import RefKind, RegistryRef, RegistryReferenceError
from raidar.grading.models import CaseStatus
from raidar.scoring.catalog.builtin import ScorerResolver
from raidar.scoring.framework.builtin_manifests import BUILTIN_MANIFESTS, CODE_QUALITY, CORRECTNESS
from raidar.scoring.framework.context import statused_result
from raidar.scoring.framework.keys import scorer_identity
from raidar.scoring.framework.manifest import (
    EvidenceInput,
    MetricSpec,
    MetricType,
    ScorerManifest,
)
from raidar.scoring.framework.ownership import (
    DuplicateMetricError,
    MetricOwnershipError,
    MissingMetricError,
)
from raidar.scoring.framework.registry import (
    RUNTIME_TOOL_LOCKS,
    ScorerRegistration,
    ScorerRegistry,
    ScorerUnavailableError,
    _descend,
)
from raidar.scoring.framework.result import (
    AssessResult,
    AssessStatus,
    Evidence,
    EvidenceSource,
    Observation,
    Span,
)
from raidar.scoring.framework.trial_inputs import blind_agent_result
from raidar.scoring.framework.validation import (
    InvalidEvidenceError,
    InvalidObservationError,
    validate_result,
    validate_scorer_set,
)
from raidar.subject.environment.python.pack_definition import (
    ToolExecution,
    ToolInstaller,
    ToolLock,
    ToolSpec,
)
from tests.scorer_support import (
    APP_V1,
    APP_V2,
    FakeScorer,
    StepFixture,
    fake_manifest,
    fake_scorer_v2,
    snapshot_context,
    two_step_context,
    write_files,
)


@pytest.fixture
def ctx(tmp_path: Path):
    return two_step_context(tmp_path)


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
    ctx = snapshot_context(
        tmp_path,
        [
            StepFixture("checkpoint_1", APP_V1, (CaseStatus.PASSED,)),
            StepFixture("checkpoint_2", latin1, (CaseStatus.PASSED,)),
        ],
    )
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


def test_scorers_never_see_arm_or_model_labels_in_structured_inputs() -> None:
    raw = {
        "n_input_tokens": 3,
        "model_usage": {"arm-model-label": {"n_input_tokens": 7}},
        "metadata": {"model": "arm-model-label"},
        "rollout_details": [{"model": "arm-model-label"}],
    }
    blinded = blind_agent_result(raw)
    assert blinded is not None
    assert "arm-model-label" not in json.dumps(blinded)
    assert blinded["n_input_tokens"] == 3
    assert blinded["model_usage"] == [
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


def test_a_scorer_without_an_implementation_has_an_identity_but_no_implementation_digest() -> None:
    registration = ScorerRegistration(fake_manifest())
    assert registration.implementation_digest() is None
    identity = scorer_identity(registration)
    assert (identity.ref, identity.implementation_digest) == (registration.manifest.ref, None)


def test_a_dotted_module_name_resolves_package_module_and_namespace_directories(
    tmp_path: Path,
) -> None:
    write_files(tmp_path, {"pkg/__init__.py": "", "mod.py": "", "ns/inner.py": ""})
    assert _descend(tmp_path, "pkg") == (tmp_path / "pkg" / "__init__.py", tmp_path / "pkg")
    assert _descend(tmp_path, "mod") == (tmp_path / "mod.py", None)
    assert _descend(tmp_path, "ns") == (None, tmp_path / "ns")
    assert _descend(tmp_path, "absent") is None


def test_evidence_names_every_line_it_cites() -> None:
    evidence = Evidence(
        metric="solved",
        subject="checkpoint_1",
        path="app/main.py",
        sha256="0" * 64,
        kind="file",
        spans=(Span(line=1), Span(line=7, end_line=9)),
    )
    assert evidence.locations() == ("app/main.py:1", "app/main.py:7")


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


def test_a_scorer_reference_resolves_to_a_digest_of_that_scorer_and_unknown_ones_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = ScorerResolver(
        ScorerRegistry(
            [ScorerRegistration(fake_manifest("1")), ScorerRegistration(fake_manifest("2"))]
        )
    )

    def resolve(ref: str) -> str:
        return resolver.resolve(RegistryRef.parse(RefKind.SCORER, ref), base_dir=tmp_path).digest

    assert resolve("fake-quality@1") == resolve("fake-quality@1")
    assert resolve("fake-quality@1") != resolve("fake-quality@2")
    with pytest.raises(RegistryReferenceError, match="unknown scorer"):
        resolve("nope@1")

    tool = ToolSpec(
        execution=ToolExecution.HOST_PARSE,
        installer=ToolInstaller.UV_ENV,
        packages=("probe-lib==1.0", "helper-lib==3.0"),
        executables=("probe",),
        modules=("probe",),
    )
    lock = ToolLock(
        version=1,
        runtime="python",
        python="3.12",
        exclude_newer="2026-01-01",
        tools={"analysis-tool": tool},
    )
    monkeypatch.setitem(RUNTIME_TOOL_LOCKS, "python", lambda: lock)
    pinned = fake_manifest("3").model_copy(
        update={
            "runtimes": ("python",),
            "tools": ("analysis-tool",),
            "execution": ToolExecution.HOST_PARSE,
        }
    )
    pinned_resolver = ScorerResolver(ScorerRegistry([ScorerRegistration(pinned)]))
    reference = RegistryRef.parse(RefKind.SCORER, pinned.ref)
    before = pinned_resolver.resolve(reference, base_dir=tmp_path)
    lock = lock.model_copy(
        update={
            "tools": {
                "analysis-tool": tool.model_copy(
                    update={"packages": ("probe-lib==2.0", "helper-lib==3.0")}
                )
            }
        }
    )
    after = pinned_resolver.resolve(reference, base_dir=tmp_path)

    assert before.tool_versions == {"probe-lib": "1.0", "helper-lib": "3.0"}
    assert after.tool_versions == {"probe-lib": "2.0", "helper-lib": "3.0"}
    assert after.digest != before.digest


def test_scorers_without_an_implementation_cannot_assess() -> None:
    registration = ScorerRegistration(CORRECTNESS)
    with pytest.raises(ScorerUnavailableError, match="no implementation"):
        registration.load()
