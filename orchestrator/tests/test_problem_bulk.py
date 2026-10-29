"""Pinned source selection and multi-problem validation through the public CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.design.problem import direct
from raidar.design.problem.catalog import select_problems, source_catalog
from raidar.design.problem.models import ProblemId
from raidar.design.problem.store import ProblemStoreError
from tests.problems_support import (
    NEGATIVE_REF,
    POSITIVE_REF,
    build_fake_scb_repo,
    build_fixture_store,
    fake_store,
)
from tests.verifier_support import LocalProcessSandbox


def test_source_selection_requires_one_pin_and_unambiguous_names(tmp_path: Path) -> None:
    checkout, commit = build_fake_scb_repo(tmp_path / "source")
    store = fake_store(tmp_path / "store", commit)
    source = f"scb@{commit}"
    _, available = source_catalog(store, source, checkout)
    expected = (ProblemId.parse(f"scb/adder@{commit[:12]}"),)
    assert available == expected
    assert select_problems(store, (), "all", source, checkout, importing=True) == expected
    assert select_problems(store, (), "adder,adder", source, checkout) == expected
    assert select_problems(store, (str(expected[0]),), None, None) == expected
    for refs, names, pin in (
        ((str(expected[0]),), "all", source),
        ((), None, source),
        ((), "missing", source),
        ((), "adder", "scb@000000000000"),
        ((), "adder", None),
    ):
        with pytest.raises(ProblemStoreError):
            select_problems(store, refs, names, pin, checkout, importing=True)


def test_bulk_import_materializes_source_selected_problems(tmp_path: Path) -> None:
    checkout, commit = build_fake_scb_repo(tmp_path / "source")
    store = fake_store(tmp_path / "store", commit)
    runner = CliRunner()
    options = ["--store", str(store.root), "--source-checkout", str(checkout)]
    source = f"scb@{commit}"
    listing = runner.invoke(main, ["problem", "list", *options, "--source", source])
    assert listing.exit_code == 0, listing.output
    ref = f"scb/adder@{commit[:12]}"
    info = runner.invoke(main, ["problem", "info", ref, *options, "--source", source])
    assert info.exit_code == 0, info.output
    result = runner.invoke(
        main, ["problem", "import", *options, "--source", source, "--problems", "all"]
    )
    assert result.exit_code == 0, result.output
    imported = store.load(ProblemId.parse(ref))
    assert tuple(step.name for step in imported.manifest.steps) == ("checkpoint_1", "checkpoint_2")
    assert imported.manifest.entry_command == "uv run --project /app app.py"


def test_bulk_validation_preserves_each_verdict_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, docker_target_offline: None
) -> None:
    store = build_fixture_store(tmp_path / "store")

    async def sandbox(problem: Any, docker_context: str) -> tuple[Any, dict[str, object]]:
        root = tmp_path / "sandbox" / problem.manifest.problem_id.problem
        return LocalProcessSandbox(root), {"sandbox": "local"}

    monkeypatch.setattr(direct, "prepare_grader_sandbox", sandbox)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "problem",
            "validate",
            "--store",
            str(store.root),
            "--problems",
            "adder,adder_negative",
            "--repeats",
            "3",
            "--concurrency",
            "2",
            "--no-audit",
        ],
    )
    assert result.exit_code == 1, result.output
    for ref, verdict in ((POSITIVE_REF, "validated_partial"), (NEGATIVE_REF, "excluded")):
        report = json.loads(store.validation_path(ProblemId.parse(ref)).read_text())
        assert report["verdict"] == verdict
        assert report["repeats"] == 3
        assert report["solvable"]["checkpoint_1"]["size"] == (3 if verdict != "excluded" else 0)
    # One aggregate table retains both results even though one problem is excluded.
    assert f"{POSITIVE_REF}  validated_partial" in result.output
    assert f"{NEGATIVE_REF}  excluded" in result.output
