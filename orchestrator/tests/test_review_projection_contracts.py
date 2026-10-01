"""Scenario-epoch read path: persisted projections, the frozen fixture and the merge."""

from __future__ import annotations

import json
import runpy
import shutil
import sys
from pathlib import Path

import pytest

from raidar.review_projection import main, merge_review_projections
from raidar.review_projection.scenario_epoch import load_experiment_projection
from raidar.review_projection.scenario_epoch.fixture import (
    LEGACY_SCENARIO,
    POLICY_MIX_RUN_PREFIX,
    frozen_projections,
    write_legacy_fixture,
    write_scenario_epoch,
)
from raidar.review_projection.scenario_epoch.fixture import main as fixture_main
from raidar.review_projection.spans import PAYLOAD_CAP, payload, to_ms


@pytest.fixture
def synthetic_experiments(tmp_path: Path) -> Path:
    root = tmp_path / "legacy-fixture"
    write_legacy_fixture(root)
    return root


def test_review_projection_helpers_cover_bounded_and_invalid_values() -> None:
    assert payload(None) is None
    text = payload("x" * (PAYLOAD_CAP + 1))
    assert text is not None
    assert text.endswith("...[truncated]")
    assert to_ms(12.9) == 12
    assert to_ms(None) is None
    assert to_ms("not-a-timestamp") is None


def test_frozen_fixture_writes_every_projection_byte_for_byte(synthetic_experiments: Path) -> None:
    frozen = frozen_projections()

    assert len(frozen) == 9
    for name, text in frozen.items():
        assert (synthetic_experiments / name / "review-projection.json").read_text() == text


def test_scenario_epoch_fixture_relabels_one_experiment_onto_the_policy_mix_problem(
    tmp_path: Path,
) -> None:
    written = write_scenario_epoch(tmp_path, policy_mix_scenario="synthetic/policy-mix@1")

    assert len(written) == 7
    assert sum(f"__{LEGACY_SCENARIO}__" in path.name for path in written) == 6
    (relabelled,) = [path for path in written if "__policy-mix__" in path.name]
    projection = load_experiment_projection(relabelled / "review-projection.json")
    assert projection.experiment.scenario == "synthetic/policy-mix@1"
    assert all(run.run.id.startswith(POLICY_MIX_RUN_PREFIX) for run in projection.runs)


def test_legacy_fixture_entrypoint_writes_and_reports_usage(tmp_path: Path, capsys) -> None:
    assert fixture_main([str(tmp_path)]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 9
    assert fixture_main([]) == 2
    assert "usage:" in capsys.readouterr().err


def test_merge_review_projections_and_cli(
    synthetic_experiments: Path,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    artifact = merge_review_projections([synthetic_experiments])
    assert len(artifact.experiments) == 9
    assert artifact.runs
    assert artifact.run_details
    assert artifact.revision_diffs
    scenario_meta = artifact.experiments[0].scenario_meta.model_dump()
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
            "--legacy-root",
            str(synthetic_experiments),
            "--output",
            str(output),
        ],
    )
    main()

    assert output.is_file()
    assert "Projected" in capsys.readouterr().out

    output.unlink()
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
        load_experiment_projection(invalid)

    legacy_root = tmp_path / "legacy"
    (legacy_root / "experiment").mkdir(parents=True)
    with pytest.raises(ValueError, match="no current-version review projections"):
        merge_review_projections([legacy_root])
    assert "Skipping legacy experiment" in capsys.readouterr().err

    source = next(path for path in synthetic_experiments.iterdir() if path.is_dir())
    duplicate_root = tmp_path / "duplicates"
    for name in ("one", "two"):
        target = duplicate_root / name
        target.mkdir(parents=True)
        shutil.copy2(source / "review-projection.json", target / "review-projection.json")
    with pytest.raises(ValueError, match="duplicate run ids"):
        merge_review_projections([duplicate_root])


def test_merge_skips_a_persisted_projection_that_fails_the_schema(
    synthetic_experiments: Path, tmp_path: Path, capsys
) -> None:
    root = tmp_path / "mixed"
    valid, stale = sorted(path for path in synthetic_experiments.iterdir() if path.is_dir())[:2]
    shutil.copytree(valid, root / valid.name)
    (root / stale.name).mkdir(parents=True)
    document = json.loads((stale / "review-projection.json").read_text(encoding="utf-8"))
    del document["experiment"]["scenario_meta"]["task_kind"]
    (root / stale.name / "review-projection.json").write_text(json.dumps(document))

    artifact = merge_review_projections([root])

    assert [record.experiment_id for record in artifact.experiments] == [
        json.loads((valid / "review-projection.json").read_text())["experiment"]["experiment_id"]
    ]
    assert "Skipping legacy experiment that fails the schema" in capsys.readouterr().err


def test_only_merge_and_fixture_import_scenario_epoch_history() -> None:
    """The import-linter list is hand-kept; this scan covers every module, new ones too."""
    import ast

    src = Path(__file__).resolve().parents[1] / "src"
    history = "raidar.review_projection.scenario_epoch"
    importers = set()
    for path in sorted((src / "raidar").rglob("*.py")):
        parts = path.relative_to(src).with_suffix("").parts
        module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        if module.startswith(history):
            continue
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = package.rsplit(".", node.level - 1)[0] if node.level else ""
                target = ".".join(part for part in (base, node.module or "") if part)
                names = [target, *(f"{target}.{alias.name}" for alias in node.names)]
            else:
                names = []
            if any(name == history or name.startswith(f"{history}.") for name in names):
                importers.add(module)
    assert importers == {"raidar.review_projection.merge", "raidar.review_projection.fixture"}
