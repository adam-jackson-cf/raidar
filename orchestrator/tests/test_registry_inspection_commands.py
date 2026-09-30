"""``raidar problem list|info`` and ``raidar init``: registry inspection and experiment starters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.packs.python.models import load_pack_spec
from raidar.problems.models import ProblemId
from raidar.problems.store import ProblemStore, ProblemStoreError
from tests.problems_support import (
    NEGATIVE_REF,
    ONESHOT_REF,
    POSITIVE_REF,
    build_fixture_store,
)


def _write_report(store: ProblemStore, ref: str, **fields: object) -> None:
    problem_id = ProblemId.parse(ref)
    manifest = store.load_manifest(problem_id)
    report = {"verdict": "validated", "manifest_sha256": manifest.digest(), "not_run": []}
    path = store.validation_path(problem_id)
    path.write_text(json.dumps(report | fields))


@pytest.fixture
def store(tmp_path: Path) -> ProblemStore:
    return build_fixture_store(tmp_path / "problems")


def test_registered_lists_every_manifest_and_skips_validation_reports(store: ProblemStore) -> None:
    _write_report(store, POSITIVE_REF)

    assert [str(problem) for problem in store.registered()] == sorted(
        [POSITIVE_REF, NEGATIVE_REF, ONESHOT_REF]
    )


def test_registered_refuses_an_entry_that_is_not_a_problem_reference(
    store: ProblemStore,
) -> None:
    stray = store.root / "registry" / "fixture" / "Not A Problem" / "fefefefefefe.json"
    stray.parent.mkdir(parents=True)
    stray.write_text("{}")

    with pytest.raises(ProblemStoreError, match="unexpected registry entry"):
        store.registered()


def test_problem_list_reports_validation_and_import_state(store: ProblemStore) -> None:
    _write_report(store, POSITIVE_REF, verdict="validated_partial", not_run=["native_parity"])
    _write_report(store, NEGATIVE_REF, verdict="excluded")
    _write_report(store, ONESHOT_REF, manifest_sha256="0" * 64)

    result = CliRunner().invoke(main, ["problem", "list", "--store", str(store.root)])

    assert result.exit_code == 0, result.output
    assert "validated_partial (usable; not run: native_parity)" in result.output
    assert "excluded (not usable)" in result.output
    assert "stale (validated for another manifest)" in result.output
    assert result.output.count("store: imported") == 3


def test_problem_list_on_an_empty_registry(tmp_path: Path) -> None:
    (tmp_path / "sources.yaml").write_text("sources: {}\n")

    result = CliRunner().invoke(main, ["problem", "list", "--store", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "No problems registered" in result.output


def test_problem_info_shows_the_manifest_and_its_state(store: ProblemStore) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["problem", "info", POSITIVE_REF, "--store", str(store.root)])

    assert result.exit_code == 0, result.output
    assert "mode: multi_checkpoint  checkpoints: 2" in result.output
    assert "steps: checkpoint_1, checkpoint_2" in result.output
    assert "validation: not validated" in result.output
    assert "store: imported" in result.output

    missing = f"fixture/absent@{ProblemId.parse(POSITIVE_REF).version}"
    result = runner.invoke(main, ["problem", "info", missing, "--store", str(store.root)])
    assert result.exit_code != 0
    assert "is not imported" in result.output


def test_problem_info_reports_a_problem_whose_content_is_not_materialised(
    store: ProblemStore,
) -> None:
    problem_id = ProblemId.parse(POSITIVE_REF)
    for part in ("task", "oracle", "references"):
        target = store.content_dir(problem_id) / part
        target.rename(target.with_name(f"{part}.moved"))

    result = CliRunner().invoke(main, ["problem", "info", POSITIVE_REF, "--store", str(store.root)])

    assert result.exit_code == 0, result.output
    assert "store: not imported" in result.output


def test_init_writes_a_validating_two_arm_starter_and_refuses_to_overwrite(
    tmp_path: Path,
) -> None:
    experiment = tmp_path / "rules-trial" / "experiment.yaml"
    runner = CliRunner()

    result = runner.invoke(main, ["init", str(experiment)])

    assert result.exit_code == 0, result.output
    text = experiment.read_text()
    codex = load_pack_spec().image.harnesses["codex-cli"]
    assert "id: rules-trial" in text
    assert f"version: {codex.version}" in text
    assert (experiment.parent / "components" / "AGENTS.md").is_file()
    validated = runner.invoke(main, ["validate", str(experiment)])
    assert validated.exit_code == 0, validated.output
    assert "arms: baseline, rules" in validated.output

    again = runner.invoke(main, ["init", str(experiment)])
    assert again.exit_code != 0
    assert "add a condition and an arm" in again.output


def test_init_rejects_an_invalid_experiment_id(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main, ["init", str(tmp_path / "x" / "experiment.yaml"), "--id", "Not Valid"]
    )

    assert result.exit_code != 0
    assert "not a valid experiment id" in result.output
    assert not (tmp_path / "x").exists()
