"""Experiment schema rules: kinds (KIND*) and naming (NAME1, NAME2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from raidar.cli import main
from raidar.design.experiment.loader import load_experiment
from raidar.design.experiment.schema import AssessmentSpec, ProblemEntry
from raidar.design.experiment.validation import SpecValidationError, spec_warnings
from tests.experiment_spec_support import (
    experiment_document,
    lock_document,
    pin_spec_digest,
    write_experiment,
)


@pytest.fixture(autouse=True)
def _fixed_spec_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    pin_spec_digest(monkeypatch)


def _issues(path: Path) -> dict[str, str]:
    with pytest.raises(SpecValidationError) as caught:
        load_experiment(path)
    return {issue.loc: issue.message for issue in caught.value.issues}


def _benchmark(**changes: Any) -> dict[str, Any]:
    """Two agents (same release, two names) on one workflow."""
    document = experiment_document(kind="benchmark", **changes)
    document["agents"].append({**document["agents"][0], "id": "second"})
    document["arms"] = [
        {"id": "first", "agent": "luna", "workflow": "raidar/one-shot@2"},
        {"id": "second", "agent": "second", "workflow": "raidar/one-shot@2"},
    ]
    document["analysis"]["baseline_arm"] = "first"
    return document


# ---- kinds ---------------------------------------------------------------------


def test_research_loop_is_reserved(tmp_path: Path) -> None:
    issues = _issues(write_experiment(tmp_path, experiment_document(kind="research-loop")))

    assert "[KIND0]" in issues["kind"]


def test_an_ab_test_compares_at_least_two_arms(tmp_path: Path) -> None:
    document = experiment_document(arms=[experiment_document()["arms"][0]])

    assert "[KIND1]" in _issues(write_experiment(tmp_path, document))["arms"]


def test_a_benchmark_compares_agents_under_one_workflow(tmp_path: Path) -> None:
    spec = load_experiment(write_experiment(tmp_path / "ok", _benchmark())).spec
    assert spec.kind == "benchmark"
    assert [w.loc for w in spec_warnings(spec)] == []

    # The tree holds one workflow, so a second one is refused as unreleased (KIND2 itself is
    # pinned on the parsed spec in test_experiment_spec_behaviors).
    two_workflows = _benchmark()
    two_workflows["arms"][1]["workflow"] = "acme/other@1"

    assert "arms.1.workflow" in _issues(write_experiment(tmp_path / "2", two_workflows))


def test_an_arm_refuses_a_bare_workflow_during_schema_validation(tmp_path: Path) -> None:
    document = _benchmark()
    document["arms"][0]["workflow"] = "one-shot@1"

    assert "arms.0.workflow" in _issues(write_experiment(tmp_path / "bare", document))


def test_rank_by_is_a_benchmark_setting_naming_an_outcome_metric(tmp_path: Path) -> None:
    ranked = _benchmark()
    ranked["analysis"]["rank_by"] = "final_solved"
    assert load_experiment(write_experiment(tmp_path / "ok", ranked)).spec.analysis.rank_by

    ab_test = experiment_document()
    ab_test["analysis"]["rank_by"] = "solve_rate"
    unknown = _benchmark()
    unknown["analysis"]["rank_by"] = "speed"

    assert "[KIND3]" in _issues(write_experiment(tmp_path / "ab", ab_test))["analysis.rank_by"]
    assert (
        "unknown outcome metric"
        in (_issues(write_experiment(tmp_path / "unknown", unknown))["analysis.rank_by"])
    )


def test_an_experiment_declares_the_factors_it_varies_beyond_its_agents(tmp_path: Path) -> None:
    plain = load_experiment(write_experiment(tmp_path / "plain", _benchmark())).spec
    assert plain.varies == ()
    assert "varies" not in plain.to_json()  # an undeclared spec keeps its lock bytes

    declared = _benchmark()
    declared["varies"] = ["harness", "model"]
    spec = load_experiment(write_experiment(tmp_path / "ok", declared)).spec
    assert spec.to_json()["varies"] == ["harness", "model"]


@pytest.mark.parametrize("varies", [["model"], ["harness", "harness"], ["colour"]])
def test_invalid_variation_declarations_are_refused(tmp_path: Path, varies: list[str]) -> None:
    document = _benchmark()
    document["varies"] = varies

    with pytest.raises(SpecValidationError):
        load_experiment(write_experiment(tmp_path, document))


def test_small_benchmarks_and_smoke_runs_validate_with_warnings(tmp_path: Path) -> None:
    small = _benchmark()
    small["analysis"]["attempts"] = 2
    smoke = experiment_document(kind="smoke")
    smoke["arms"] = smoke["arms"][:1]

    for name, document, location in (
        ("benchmark", small, "analysis.attempts"),
        ("smoke", smoke, "kind"),
    ):
        path = write_experiment(tmp_path / name, document)
        warnings = spec_warnings(load_experiment(path).spec)
        assert [warning.loc for warning in warnings] == [location]
        result = CliRunner().invoke(main, ["validate", str(path)])
        assert result.exit_code == 0, result.output
        assert location in result.output
    small["analysis"]["attempts"] = 5
    assert spec_warnings(load_experiment(write_experiment(tmp_path / "enough", small)).spec) == []


def test_the_kind_is_part_of_the_execution_identity(tmp_path: Path) -> None:
    smoke = experiment_document(kind="smoke")
    ab_test = lock_document(write_experiment(tmp_path / "ab", experiment_document()))
    relabelled = lock_document(write_experiment(tmp_path / "smoke", smoke))

    assert ab_test["execution"]["design"]["kind"] == "ab-test"
    assert relabelled["execution"]["design"]["kind"] == "smoke"
    assert ab_test["lock_sha256"] != relabelled["lock_sha256"]


# ---- local problem paths and scorer configuration ---------------------------------


@pytest.mark.parametrize("path", ["", ".", "/abs/task", "..", "a/../b", "a\\b"])
def test_a_local_problem_path_must_stay_inside_the_experiment_directory(path: str) -> None:
    with pytest.raises(ValidationError):
        ProblemEntry.model_validate({"path": path})


def test_a_local_problem_path_is_normalised_and_takes_no_checkpoint_selection() -> None:
    assert ProblemEntry.model_validate({"path": "./tasks/forge"}).path == "tasks/forge"
    with pytest.raises(ValidationError, match="selects within a registry"):
        ProblemEntry.model_validate({"path": "tasks/forge", "checkpoints": 2})


@pytest.mark.parametrize(
    "config",
    [{"x": float("nan")}, {"x": [1, {"y": float("inf")}]}, {"x": {"y": float("-inf")}}],
    ids=["nan", "list-nested", "dict-nested"],
)
def test_scorer_config_must_be_strict_json(config: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="non-finite number"):
        AssessmentSpec.model_validate(
            {"scorers": ["correctness@10"], "scorer_config": {"correctness@10": config}}
        )


def test_finite_scorer_config_is_accepted() -> None:
    config = {"x": [1, 2.5, {"y": None}]}

    spec = AssessmentSpec.model_validate(
        {"scorers": ["correctness@10"], "scorer_config": {"correctness@10": config}}
    )

    assert spec.scorer_config["correctness@10"] == config


# ---- naming --------------------------------------------------------------------


def _at(root: Path, kind: str, experiment_id: str, document: dict[str, Any]) -> Path:
    directory = root / "experiments" / kind / experiment_id
    directory.mkdir(parents=True)
    return write_experiment(directory, document)


def test_name1_the_path_names_the_kind_and_the_id(tmp_path: Path) -> None:
    good = _at(tmp_path, "ab-test", "python__fixture__test", experiment_document())
    wrong_kind = _at(tmp_path, "benchmark", "python__fixture__test", experiment_document())
    wrong_dir = _at(tmp_path, "ab-test", "python__other__test", experiment_document())

    assert load_experiment(good).spec.id == "python__fixture__test"
    assert "[NAME1]" in _issues(wrong_kind)["kind"]
    assert "experiments/ab-test/" in _issues(wrong_kind)["kind"]
    assert "[NAME1]" in _issues(wrong_dir)["id"]


def test_name1_does_not_apply_outside_an_experiments_root(tmp_path: Path) -> None:
    assert load_experiment(write_experiment(tmp_path / "anywhere", experiment_document())).spec


@pytest.mark.parametrize(
    "relative",
    ["ab-test/python__fixture__test/nested", "ab-test", "python__fixture__test"],
)
def test_name1_applies_at_any_depth_below_an_experiments_root(
    tmp_path: Path, relative: str
) -> None:
    path = write_experiment(tmp_path / "experiments" / relative, experiment_document())

    assert "experiments/<kind>/<id>/experiment.yaml" in _issues(path)["id"]


def test_name1_requires_the_experiment_file_name(tmp_path: Path) -> None:
    directory = tmp_path / "experiments" / "ab-test" / "python__fixture__test"
    directory.mkdir(parents=True)
    renamed = write_experiment(directory, experiment_document()).rename(directory / "copy.yaml")

    assert "[NAME1]" in _issues(renamed)["id"]


@pytest.mark.parametrize(
    "experiment_id",
    ["fixture", "python__fixture", "node-ts__fixture__test", "python____test", "a__b__c__d"],
)
def test_name2_the_id_is_runtime_subject_intent(tmp_path: Path, experiment_id: str) -> None:
    issues = _issues(write_experiment(tmp_path, experiment_document(id=experiment_id)))

    assert "[NAME2]" in issues["id"]


def test_naming_and_kind_issues_are_reported_with_the_others_at_once(tmp_path: Path) -> None:
    document = experiment_document(id="fixture", arms=[experiment_document()["arms"][0]])
    document["analysis"]["baseline_arm"] = "missing"

    issues = _issues(write_experiment(tmp_path, document))

    assert {"id", "arms", "analysis.baseline_arm"} <= set(issues)
