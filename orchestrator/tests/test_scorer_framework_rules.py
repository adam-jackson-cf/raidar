"""Scorer framework rules: manifests, registrations, results and result validation.

A malformed declaration or result is refused with the rule it breaks; a trajectory-level
evidence item may point into any step's stored tree; an unknown name never resolves.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from raidar.contract.references import RegistryReferenceError
from raidar.scoring.catalog.outcomes import declared_direction
from raidar.scoring.framework import registry as registry_module
from raidar.scoring.framework.manifest import Direction, MetricSpec, ScorerManifest
from raidar.scoring.framework.registry import (
    ScorerRegistration,
    ScorerRegistry,
    ScorerUnavailableError,
    tool_lock_for,
)
from raidar.scoring.framework.result import (
    TRAJECTORY_SUBJECT,
    AssessStatus,
    Evidence,
    Exclusion,
    Observation,
    Span,
)
from raidar.scoring.framework.validation import validate_result
from tests.scorer_support import APP_V1, FakeScorer, fake_manifest, two_step_context


def _manifest(**changes: Any) -> dict[str, Any]:
    return {**fake_manifest().model_dump(mode="json"), **changes}


def _metric(**changes: Any) -> MetricSpec:
    return MetricSpec.model_validate({**fake_manifest().metrics[2].model_dump(), **changes})


@pytest.mark.parametrize("bounds", [(2.0, 1.0), (0.0, math.inf), (math.nan, 1.0)])
def test_a_metric_range_must_be_finite_and_ordered(bounds: tuple[float, float]) -> None:
    with pytest.raises(ValidationError, match="range must be finite with low <= high"):
        _metric(range=bounds)


@pytest.mark.parametrize("value", [math.nan, math.inf])
def test_a_non_finite_value_is_not_admitted(value: float) -> None:
    assert _metric().admits(value) == "value must be finite"


def test_an_observation_value_must_be_finite() -> None:
    with pytest.raises(ValidationError, match="value must be finite"):
        Observation(metric="m", subject="s", status=AssessStatus.OK, value=math.inf)


def test_a_span_cannot_end_before_it_starts() -> None:
    with pytest.raises(ValidationError, match="end_line precedes line"):
        Span(line=3, end_line=2)


def test_a_runtime_neutral_manifest_cannot_name_other_runtimes_or_pack_tools() -> None:
    with pytest.raises(ValidationError, match="cannot be combined with others"):
        ScorerManifest.model_validate(_manifest(runtimes=["*", "python"]))
    with pytest.raises(ValidationError, match="cannot pin pack tools"):
        ScorerManifest.model_validate(_manifest(tools=["ruff"]))


def test_a_metric_cannot_read_an_input_the_scorer_does_not_declare() -> None:
    metrics = _manifest()["metrics"]
    metrics[0]["inputs"] = ["trace"]

    with pytest.raises(ValidationError, match="reads undeclared inputs"):
        ScorerManifest.model_validate(_manifest(metrics=metrics))


@pytest.mark.parametrize("target", ["module", "a:b:c"])
def test_an_implementation_target_must_be_module_colon_attribute(target: str) -> None:
    with pytest.raises(ValueError, match="must be 'module:attribute'"):
        ScorerRegistration(fake_manifest(), target)


def test_a_ref_registered_twice_is_refused_and_an_unknown_ref_names_the_known_ones() -> None:
    registration = ScorerRegistration(fake_manifest())

    with pytest.raises(ValueError, match="duplicate scorer registration fake-quality@1"):
        ScorerRegistry([registration, registration])
    with pytest.raises(RegistryReferenceError, match="known: fake-quality@1"):
        ScorerRegistry([registration]).get("nope@1")


@pytest.mark.parametrize("module", ["raidar.scoring.no_such_module_zz", "no_such_top_level_zz"])
def test_an_implementation_module_that_does_not_exist_has_no_digest(module: str) -> None:
    registration = ScorerRegistration(fake_manifest(), f"{module}:scorer")

    with pytest.raises(ScorerUnavailableError, match="not found"):
        registration.implementation_digest()


def test_two_implementation_modules_digest_together_and_differ_from_either_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "isolated_scorers"
    package.mkdir()
    (package / "a.py").write_text("x = 1\n")
    (package / "b.py").write_text("x = 2\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    first, second = "isolated_scorers.a", "isolated_scorers.b"
    both = ScorerRegistration(fake_manifest(), f"{first}:x", f"{second}:y").implementation_digest()
    only_first = ScorerRegistration(fake_manifest(), f"{first}:x").implementation_digest()
    only_second = ScorerRegistration(fake_manifest(), f"{second}:x").implementation_digest()

    assert both not in (only_first, only_second)
    assert only_first != only_second


def test_namespace_directories_contribute_no_source_to_a_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "zz_namespace" / "inner").mkdir(parents=True)
    (tmp_path / "zz_namespace" / "inner" / "leaf.py").write_text("x = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    files = registry_module._module_files("zz_namespace.inner.leaf")

    assert files == (("zz_namespace.inner.leaf", tmp_path / "zz_namespace" / "inner" / "leaf.py"),)


def test_pack_tools_need_a_runtime_with_a_lock_and_a_locked_tool_name() -> None:
    unlocked = ScorerManifest.model_validate(_manifest(runtimes=["node"], tools=["ruff"]))
    unknown = ScorerManifest.model_validate(_manifest(runtimes=["python"], tools=["no-such-tool"]))

    with pytest.raises(RegistryReferenceError, match="runtime 'node' has no tool lock"):
        tool_lock_for(unlocked)
    with pytest.raises(RegistryReferenceError, match="no-such-tool"):
        tool_lock_for(unknown)


def test_a_metric_the_manifest_does_not_declare_has_no_direction() -> None:
    manifest = fake_manifest()

    assert declared_direction(manifest, "growth") is Direction.HIGHER_BETTER
    with pytest.raises(ValueError, match="declares no metric 'nope'"):
        declared_direction(manifest, "nope")


def test_an_unknown_step_name_is_a_key_error(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="unknown step 'nope'"):
        two_step_context(tmp_path).step("nope")


def _result_with(tmp_path: Path, **changes: Any):  # type: ignore[no-untyped-def]
    ctx = two_step_context(tmp_path)
    manifest = fake_manifest()
    return manifest, ctx, FakeScorer(manifest).assess(ctx).model_copy(update=changes)


def test_trajectory_evidence_may_point_into_any_steps_stored_tree(tmp_path: Path) -> None:
    sha256 = hashlib.sha256(APP_V1["app/main.py"].encode()).hexdigest()
    cited = Evidence(
        metric="solved_checkpoints",
        subject=TRAJECTORY_SUBJECT,
        path="app/main.py",
        sha256=sha256,
        kind="file",
        spans=(Span(line=1),),
    )
    manifest, ctx, result = _result_with(tmp_path, evidence=(cited,))

    assert validate_result(manifest, ctx, result).evidence == (cited,)

    missing = cited.model_copy(update={"path": "app/gone.py"})
    manifest, ctx, result = _result_with(tmp_path / "second", evidence=(missing,))
    with pytest.raises(ValueError, match="no such file"):
        validate_result(manifest, ctx, result)


def test_exclusions_must_name_a_step_or_the_trajectory(tmp_path: Path) -> None:
    stray = Exclusion(subject="checkpoint_9", path="a.py", reason="why")
    manifest, ctx, result = _result_with(tmp_path, exclusions=(stray,))

    with pytest.raises(ValueError, match="unknown subjects \\['checkpoint_9'\\]"):
        validate_result(manifest, ctx, result)
