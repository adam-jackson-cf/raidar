"""Experiment schema rules: kinds (KIND*) and naming (NAME1, NAME2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.spec.loader import load_experiment
from raidar.spec.validation import SpecValidationError, spec_warnings
from tests.experiment_spec_support import (
    AGENTS_BENCHMARK,
    EXPERIMENTS,
    MECHANICS_SMOKE,
    SMOKE_EXPERIMENT,
    experiment_document,
    write_experiment,
)

CLAUDE = {
    "id": "claude",
    "name": "claude-code",
    "provider": "anthropic",
    "model": "claude-haiku-4-5",
    "version": "2.1.285",
}


def _issues(path: Path) -> dict[str, str]:
    with pytest.raises(SpecValidationError) as caught:
        load_experiment(path)
    return {issue.loc: issue.message for issue in caught.value.issues}


def _benchmark(**changes: Any) -> dict[str, Any]:
    document = experiment_document(**{"kind": "benchmark", "factors": ["agent"], **changes})
    document["agents"].append(dict(CLAUDE))
    document["conditions"] = [{"id": "baseline", "components": []}]
    document["arms"] = [
        {"id": "codex", "condition": "baseline", "agent": "luna"},
        {"id": "claude", "condition": "baseline", "agent": "claude"},
    ]
    document["analysis"]["baseline_arm"] = "codex"
    return document


# ---- kinds ---------------------------------------------------------------------


def test_every_committed_experiment_is_in_its_kind_directory() -> None:
    files = sorted(EXPERIMENTS.glob("*/*/experiment.yaml"))
    assert files == sorted([SMOKE_EXPERIMENT, AGENTS_BENCHMARK, MECHANICS_SMOKE])
    for path in files:
        spec = load_experiment(path).spec
        assert (path.parents[1].name, path.parent.name) == (spec.kind, spec.id)
        assert spec.id.split("__")[0] == spec.runtime == "python"


def test_research_loop_is_reserved(tmp_path: Path) -> None:
    issues = _issues(write_experiment(tmp_path, experiment_document(kind="research-loop")))

    assert "[KIND0]" in issues["kind"]


@pytest.mark.parametrize(
    ("changes", "loc"),
    [
        ({"factors": ["agent.model"]}, "factors"),
        ({"factors": []}, "factors"),
        ({"conditions": [{"id": "baseline", "components": []}]}, "conditions"),
    ],
)
def test_an_ab_test_compares_conditions(tmp_path: Path, changes: dict[str, Any], loc: str) -> None:
    document = experiment_document(**changes)
    if "conditions" in changes:
        document["arms"] = [{"id": "baseline", "condition": "baseline", "agent": "luna"}]
    issues = _issues(write_experiment(tmp_path, document))

    assert "[KIND1]" in issues[loc]


def test_an_ab_test_may_add_agent_factors(tmp_path: Path) -> None:
    document = experiment_document(factors=["condition", "agent"])
    document["agents"].append(dict(CLAUDE))
    document["arms"].append({"id": "claude-guided", "condition": "guided", "agent": "claude"})

    assert load_experiment(write_experiment(tmp_path, document)).spec.kind == "ab-test"


def test_a_benchmark_compares_agents_under_one_condition(tmp_path: Path) -> None:
    spec = load_experiment(write_experiment(tmp_path / "ok", _benchmark())).spec
    assert spec.kind == "benchmark"
    assert [w.loc for w in spec_warnings(spec)] == []

    with_condition = _benchmark(factors=["condition", "agent"])
    two_conditions = _benchmark()
    two_conditions["conditions"].append({"id": "guided", "components": []})
    no_factors = _benchmark(factors=[])

    assert "[KIND2]" in _issues(write_experiment(tmp_path / "c", with_condition))["factors"]
    assert "[KIND2]" in _issues(write_experiment(tmp_path / "2", two_conditions))["conditions"]
    assert "[KIND2]" in _issues(write_experiment(tmp_path / "0", no_factors))["factors"]


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


def test_small_benchmarks_and_smoke_runs_validate_with_warnings(tmp_path: Path) -> None:
    small = _benchmark()
    small["analysis"]["attempts"] = 2
    smoke = experiment_document(kind="smoke", factors=[])
    smoke["conditions"] = smoke["conditions"][:1]
    smoke["arms"] = smoke["arms"][:1]
    runner = CliRunner()

    small_result = runner.invoke(main, ["validate", str(write_experiment(tmp_path / "b", small))])
    smoke_result = runner.invoke(main, ["validate", str(write_experiment(tmp_path / "s", smoke))])

    assert small_result.exit_code == 0, small_result.output
    assert "below the 5 attempts per agent" in small_result.output
    assert smoke_result.exit_code == 0, smoke_result.output
    assert "not citable" in smoke_result.output


def test_the_kind_is_part_of_the_execution_identity(tmp_path: Path) -> None:
    from tests.test_experiment_lock_behaviors import _lock

    smoke = experiment_document(kind="smoke")
    ab_test = _lock(write_experiment(tmp_path / "ab", experiment_document()))
    relabelled = _lock(write_experiment(tmp_path / "smoke", smoke))

    assert ab_test["execution"]["design"]["kind"] == "ab-test"
    assert ab_test["lock_sha256"] != relabelled["lock_sha256"]


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
    document = experiment_document(id="fixture", factors=["agent.model"])
    document["agents"][0]["name"] = "codex"
    document["analysis"]["baseline_arm"] = "missing"

    issues = _issues(write_experiment(tmp_path, document))

    assert {"id", "factors", "analysis.baseline_arm"} <= set(issues)
