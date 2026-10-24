"""The review projection contract: helpers, the CLI entrypoint and fail-closed merging."""

from __future__ import annotations

import json
import runpy
import shutil
import sys
from pathlib import Path

import pytest

from raidar.contracts.review_contract import ReviewProjectionArtifact
from raidar.evaluation.review.projection import main, merge_review_projections
from raidar.evaluation.review.projection.fixture import (
    JOBS_DIR,
    REVIEW_ONLY_DIR,
    generate_review_fixture,
)
from raidar.evaluation.review.projection.spans import PAYLOAD_CAP, payload, to_ms


@pytest.fixture(scope="module")
def fixture_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("review-fixture")
    generate_review_fixture(root)
    return root


def test_review_projection_helpers_cover_bounded_and_invalid_values() -> None:
    assert payload(None) is None
    text = payload("x" * (PAYLOAD_CAP + 1))
    assert text is not None
    assert text.endswith("...[truncated]")
    assert to_ms(12.9) == 12
    assert to_ms(None) is None
    assert to_ms("not-a-timestamp") is None


def test_projection_wire_contract_uses_problem_and_arm(fixture_root: Path) -> None:
    artifact = merge_review_projections(
        job_roots=[fixture_root / JOBS_DIR], stores=[fixture_root / "store"]
    )
    document = json.loads(artifact.model_dump_json())
    assert document["schema_version"] == 2
    assert "revision_diffs" not in document
    [diff] = document["arm_diffs"]
    assert {"problem", "from_arm", "to_arm"} <= diff.keys()
    assert set(diff["files"]) == {"condition", "prompt"}
    run = document["runs"][0]
    assert {"problem", "arm"} <= run.keys()
    assert not {"scenario", "revision"} & run.keys()
    experiment = document["experiments"][0]
    assert {"problem", "arm", "problem_meta"} <= experiment.keys()
    assert not {"scenario", "revision", "scenario_meta"} & experiment.keys()
    assert set(experiment["problem_meta"]) == {
        "display_name",
        "description",
        "difficulty",
        "languages",
        "checkpoints",
        "timeout_sec",
    }
    assert experiment["comparable_config"]["fingerprint"]


def test_review_projection_cli_writes_the_artifact(
    fixture_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    output = tmp_path / "review.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "review_projection",
            "--jobs-root",
            str(fixture_root / REVIEW_ONLY_DIR),
            "--store",
            str(fixture_root / "store"),
            "--output",
            str(output),
        ],
    )
    main()

    artifact = ReviewProjectionArtifact.model_validate_json(output.read_text())
    assert {run.problem for run in artifact.runs} == {"synthetic/review-only@1"}
    assert "Projected 3 run(s) across 1 experiment(s)" in capsys.readouterr().out

    output.unlink()
    runpy.run_module("raidar.evaluation.review.projection", run_name="__main__")
    assert output.is_file()


def test_review_projection_fails_closed_without_runs_or_with_duplicate_ids(
    fixture_root: Path, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="no projectable runs found under"):
        merge_review_projections(job_roots=[tmp_path / "empty"])
    with pytest.raises(SystemExit, match="--jobs-root"):
        main(["--output", str(tmp_path / "out.json")])

    source = fixture_root / REVIEW_ONLY_DIR
    for name in ("one", "two"):
        shutil.copytree(source, tmp_path / name)
    with pytest.raises(ValueError, match="duplicate job ids"):
        merge_review_projections(job_roots=[tmp_path / "one", tmp_path / "two"])
