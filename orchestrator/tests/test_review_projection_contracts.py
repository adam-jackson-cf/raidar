"""Boundary coverage for typed scenario and review projections."""

from __future__ import annotations

import json
import runpy
import shutil
import sys
from pathlib import Path

import pytest

from raidar.application.scenario_catalog import load_scenario
from raidar.review_projection import (
    PAYLOAD_CAP,
    _load_experiment_projection,
    _payload,
    _summary_objects,
    _to_ms,
    _trace_name,
    build_experiment_review_projection,
    main,
    merge_review_projections,
)
from raidar.review_scenario_projection import (
    revision_diff_artifact,
    scenario_comparable_config,
    scenario_review_contract,
)
from raidar.synthetic import generate_synthetic_benchmark

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def synthetic_experiments(tmp_path: Path) -> Path:
    root = tmp_path / "experiments"
    generate_synthetic_benchmark(root)
    return root


def test_scenario_projection_hashes_artifacts_and_loads_parent_revision() -> None:
    initial_dir = REPO_ROOT / "scenarios/hello-world-smoke/v001"
    initial = load_scenario(initial_dir / "scenario.yaml")
    comparable = scenario_comparable_config(initial, scenario_dir=initial_dir)

    assert comparable.display_name == initial.display_name
    assert comparable.task_kind == initial.task_kind
    assert comparable.delivery_mode == initial.delivery_mode
    assert comparable.workspace_state == initial.workspace_state
    assert comparable.starting_conditions == initial.starting_conditions
    assert comparable.languages == initial.languages
    assert comparable.evaluation_role == initial.evaluation_role
    assert comparable.catalog_status == initial.catalog_status
    assert comparable.family_id == initial.family_id
    assert comparable.variant_axes == initial.variant_axes
    assert "category" not in comparable.model_dump()

    assert comparable.prompt_artifacts[initial.prompt.entry]
    assert comparable.rules_artifacts
    assert scenario_comparable_config(initial).prompt_artifacts == {}

    revision_dir = REPO_ROOT / "scenarios/skill-benchmark-coding-test/v002"
    revision = load_scenario(revision_dir / "scenario.yaml")
    review = scenario_review_contract(revision, scenario_dir=revision_dir)

    assert review.revision_diff is not None
    assert review.revision_diff.from_revision == "v001"
    assert review.revision_diff.to_revision == "v002"


def test_scenario_projection_rejects_missing_or_mismatched_parent(tmp_path: Path) -> None:
    initial_dir = REPO_ROOT / "scenarios/hello-world-smoke/v001"
    initial = load_scenario(initial_dir / "scenario.yaml")
    before = scenario_comparable_config(initial)
    other = before.model_copy(update={"scenario": "other"})

    with pytest.raises(ValueError, match="same scenario"):
        revision_diff_artifact(before, other)

    missing_parent = initial.model_copy(update={"parent_revision": "v999"})
    revision_dir = tmp_path / "demo/v002"
    revision_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="parent revision scenario is required"):
        scenario_review_contract(missing_parent, scenario_dir=revision_dir)


def test_review_projection_helpers_cover_bounded_and_invalid_values() -> None:
    assert _payload(None) is None
    assert _payload("x" * (PAYLOAD_CAP + 1)).endswith("...[truncated]")
    assert _to_ms(12.9) == 12
    assert _to_ms(None) is None
    assert _to_ms("not-a-timestamp") is None
    assert _trace_name("tool_call", {"name": "read"}) == "tool read"

    with pytest.raises(ValueError, match="aggregate must be an object"):
        _summary_objects({"aggregate": [], "sample": {}, "rerun": {}, "findings": []})
    with pytest.raises(ValueError, match="findings must be an array"):
        _summary_objects({"aggregate": {}, "sample": {}, "rerun": {}, "findings": [1]})


def test_review_projection_rejects_invalid_summary_configuration(
    synthetic_experiments: Path,
) -> None:
    experiment_dir = next(path for path in synthetic_experiments.iterdir() if path.is_dir())
    summary = json.loads((experiment_dir / "experiment-summary.json").read_text("utf-8"))

    with pytest.raises(ValueError, match="config must be an object"):
        build_experiment_review_projection(experiment_dir, {"config": []}, [])

    invalid_reasoning = json.loads(json.dumps(summary))
    invalid_reasoning["config"]["reasoning_effort"] = 7
    with pytest.raises(ValueError, match="reasoning_effort must be a string or null"):
        build_experiment_review_projection(experiment_dir, invalid_reasoning, [])


def test_merge_review_projections_and_cli(
    synthetic_experiments: Path,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    artifact = merge_review_projections(synthetic_experiments)
    assert len(artifact.experiments) == 9
    assert artifact.runs
    assert artifact.run_details
    assert artifact.revision_diffs
    scenario_meta = artifact.experiments[0].scenario_meta
    scenario_meta = scenario_meta.model_dump()
    assert {
        "display_name",
        "task_kind",
        "delivery_mode",
        "workspace_state",
        "starting_conditions",
        "languages",
        "evaluation_role",
        "catalog_status",
        "family_id",
        "variant_axes",
    } <= scenario_meta.keys()
    assert "category" not in scenario_meta

    output = tmp_path / "review.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "review_projection",
            "--experiments-root",
            str(synthetic_experiments),
            "--output",
            str(output),
        ],
    )
    main()

    assert output.is_file()
    assert "Projected" in capsys.readouterr().out

    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        runpy.run_module("raidar.review_projection", run_name="__main__")
    assert output.is_file()


def test_review_projection_loader_and_merge_fail_closed(
    synthetic_experiments: Path,
    tmp_path: Path,
    capsys,
) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid review projection"):
        _load_experiment_projection(invalid)

    legacy_root = tmp_path / "legacy"
    (legacy_root / "experiment").mkdir(parents=True)
    with pytest.raises(ValueError, match="no current-version review projections"):
        merge_review_projections(legacy_root)
    assert "Skipping legacy experiment" in capsys.readouterr().err

    source = next(path for path in synthetic_experiments.iterdir() if path.is_dir())
    duplicate_root = tmp_path / "duplicates"
    for name in ("one", "two"):
        target = duplicate_root / name
        target.mkdir(parents=True)
        shutil.copy2(source / "review-projection.json", target / "review-projection.json")
    with pytest.raises(ValueError, match="duplicate run ids"):
        merge_review_projections(duplicate_root)
