"""Schema v2: experiment kinds (KIND*), naming (NAME1, NAME2) and read-only schema v1 files."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from raidar.cli import main
from raidar.spec.loader import (
    LegacySpecError,
    load_experiment,
    load_legacy_experiment,
)
from raidar.spec.schema_v1 import infer_kind, upgrade_document
from raidar.spec.validation import SpecValidationError, spec_warnings
from tests.experiment_spec_support import (
    AGENTS_BENCHMARK,
    EXPERIMENTS,
    GUARDRAILS_EXPERIMENT,
    GUIDED_R3_EXPERIMENT,
    LEGACY_V1,
    PILOT_EXPERIMENT,
    PILOT_ONESHOT,
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


def test_every_committed_experiment_is_schema_v2_in_its_kind_directory() -> None:
    files = sorted(EXPERIMENTS.glob("*/*/experiment.yaml"))
    assert files == sorted(
        [
            PILOT_EXPERIMENT,
            PILOT_ONESHOT,
            SMOKE_EXPERIMENT,
            AGENTS_BENCHMARK,
            GUIDED_R3_EXPERIMENT,
            GUARDRAILS_EXPERIMENT,
        ]
    )
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


# ---- schema v1 -------------------------------------------------------------------


LEGACY = {
    "pilot-forge": ("ab-test", PILOT_EXPERIMENT),
    "pilot-circuit-eval-oneshot": ("ab-test", PILOT_ONESHOT),
    "smoke": ("smoke", SMOKE_EXPERIMENT),
    "smoke-agents": ("benchmark", AGENTS_BENCHMARK),
}


# Decisions taken after the v1 -> v2 translation that the committed v2 files carry:
# 2026-10-04, Jev parked: experiments declare process-compliance@2, not @1.
_LATER_DECISIONS = {"process-compliance@1": "process-compliance@2"}


def _later_decisions(document: dict[str, Any]) -> dict[str, Any]:
    assessment = document.get("assessment") or {}
    if "scorers" in assessment:
        assessment["scorers"] = [_LATER_DECISIONS.get(ref, ref) for ref in assessment["scorers"]]
    return document


@pytest.mark.parametrize("name", sorted(LEGACY))
def test_v1_specs_stay_loadable_read_only(name: str, tmp_path: Path) -> None:
    kind, current = LEGACY[name]
    source = LEGACY_V1 / f"{name}.experiment.yaml"
    legacy = load_legacy_experiment(source)
    runner = CliRunner()

    validated = runner.invoke(main, ["validate", str(source)])
    overridden = runner.invoke(main, ["validate", str(source), "--set", "analysis.blocks=2"])
    locked = runner.invoke(main, ["lock", str(source), "--output", str(tmp_path / "x.json")])
    planned = runner.invoke(main, ["plan", str(source)])

    assert legacy.spec_v1.id == name
    assert legacy.upgraded.kind == kind == infer_kind(yaml.safe_load(source.read_text()))
    assert validated.exit_code == 0, validated.output
    assert "schema_version 1, read-only" in validated.output
    assert overridden.exit_code != 0
    assert "read-only" in overridden.output
    for refused in (locked, planned):
        assert refused.exit_code != 0
        assert "schema_version 1 is read-only" in refused.output
    assert not (tmp_path / "x.json").exists()
    with pytest.raises(LegacySpecError):
        load_experiment(source)
    # The committed v2 experiment is exactly the translation of the v1 file, apart from
    # later recorded decisions (_LATER_DECISIONS).
    upgraded = _later_decisions(
        upgrade_document(
            yaml.safe_load(source.read_text()), kind=kind, experiment_id=current.parent.name
        )
    )
    committed = yaml.safe_load(current.read_text())
    assert {k: v for k, v in upgraded.items() if k != "description"} == {
        k: v for k, v in committed.items() if k != "description"
    }


def test_upgrade_spec_writes_a_valid_v2_file_into_the_kind_layout(tmp_path: Path) -> None:
    source = LEGACY_V1 / "pilot-forge.experiment.yaml"
    experiment_id = "python__forge__guided-pilot"
    output = tmp_path / "experiments" / "ab-test" / experiment_id / "experiment.yaml"
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["upgrade-spec", str(source), "--kind", "ab-test", "--id", experiment_id]
        + ["--output", str(output)],
    )
    wrong_kind = runner.invoke(
        main,
        ["upgrade-spec", str(source), "--kind", "benchmark", "--id", experiment_id]
        + ["--output", str(tmp_path / "experiments" / "benchmark" / experiment_id / "e.yaml")],
    )
    not_v1 = runner.invoke(
        main,
        ["upgrade-spec", str(PILOT_EXPERIMENT), "--kind", "ab-test", "--id", experiment_id]
        + ["--output", str(tmp_path / "other.yaml")],
    )

    assert result.exit_code == 0, result.output
    shutil.copytree(PILOT_EXPERIMENT.parent / "components", output.parent / "components")
    spec = load_experiment(output).spec
    assert (spec.schema_version, spec.kind, spec.id) == (2, "ab-test", experiment_id)
    assert spec.agents[0].name.value == "codex"
    assert spec.analysis.attempts == 16
    assert wrong_kind.exit_code != 0
    assert "[KIND2]" in wrong_kind.output
    assert not_v1.exit_code != 0
    assert "only schema_version 1" in not_v1.output
