"""``raidar migrate-run-lock``: a version-1 run moves only when its execution is provably equal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.execution.lock_migration import EXECUTED_LOCK_FILE
from raidar.spec.canonical import digest_json
from raidar.spec.loader import load_experiment
from raidar.spec.lock import build_lock, render_lock
from raidar.spec.policies import PolicyResolver
from raidar.spec.references import RefKind, ResolverSet
from tests.experiment_spec_support import (
    InMemoryResolver,
    experiment_document,
    fixture_resolvers,
    write_experiment,
)


def _current(path: Path) -> dict[str, Any]:
    _, component, scorer = fixture_resolvers()
    # The problem pins a grading tool, so the execution section records a tool too.
    problem = InMemoryResolver(
        RefKind.PROBLEM, {"scb/forge@abc123": {"checkpoints": 8}}, tool_versions={"scb-check": "1"}
    )
    resolvers = ResolverSet([problem, component, scorer, PolicyResolver()], entry_point_group=None)
    lock_path = path.with_name("experiment.lock.json")
    rendered = render_lock(
        build_lock(load_experiment(path), lock_path=lock_path, resolvers=resolvers)
    )
    lock_path.write_bytes(rendered)
    return json.loads(rendered)


def _version_one(current: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """The version-1 layout of ``current``: one flat body whose digest was lock_sha256."""
    execution, assessment = current["execution"], current["assessment"]
    body: dict[str, Any] = {
        "lock_version": 1,
        "experiment": current["experiment"],
        "overrides": current["overrides"],
        "spec": current["spec"],
        "problems": execution["problems"],
        "files": execution["files"],
        "references": {**execution["references"], **assessment["references"]},
        "conditions": execution["conditions"],
        "arms": execution["arms"],
        "headline_policy": assessment["headline_policy"],
        "versions": {
            **current["toolchain"],
            **execution["versions"],
            "tools": {**execution["tools"], **assessment["tools"]},
        },
    }
    body = json.loads(json.dumps(body))
    for dotted, value in changes.items():
        *parents, leaf = dotted.split(".")
        target = body
        for key in parents:
            target = target[key]
        target[leaf] = value
    return {**body, "lock_sha256": digest_json(body)}


def _run(root: Path, executed: dict[str, Any]) -> Path:
    run_dir = root / "runs" / "fixture" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {"kind": "raidar-run", "run_id": "run-1", "lock_sha256": executed["lock_sha256"]}
        )
    )
    (run_dir / "plan.json").write_text(json.dumps({"lock_sha256": executed["lock_sha256"]}))
    (root / "executed.lock.json").write_text(json.dumps(executed, indent=2))
    return run_dir


def _migrate(root: Path, run_dir: Path) -> Any:
    return CliRunner().invoke(
        main,
        [
            "migrate-run-lock",
            str(run_dir),
            "--executed-lock",
            str(root / "executed.lock.json"),
            "--lock",
            str(root / "experiment.lock.json"),
        ],
    )


@pytest.fixture
def experiment(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    path = write_experiment(tmp_path, experiment_document())
    return path, _current(path)


def test_a_run_whose_scorers_changed_moves_to_the_current_lock(
    tmp_path: Path, experiment: tuple[Path, dict[str, Any]]
) -> None:
    _, current = experiment
    scorer = "references.scorer.correctness@1.digest"
    executed = _version_one(current, **{scorer: "f" * 64})
    run_dir = _run(tmp_path, executed)
    plan_before = (run_dir / "plan.json").read_bytes()

    first = _migrate(tmp_path, run_dir)
    again = _migrate(tmp_path, run_dir)

    assert first.exit_code == 0, first.output
    manifest = json.loads((run_dir / "run.json").read_text())
    assert manifest["lock_sha256"] == current["lock_sha256"]
    assert manifest["lock_migration"]["executed_lock_sha256"] == executed["lock_sha256"]
    assert json.loads((run_dir / EXECUTED_LOCK_FILE).read_text()) == executed
    # The executed record stays as it was: plan.json keeps the digest the trials ran under.
    assert (run_dir / "plan.json").read_bytes() == plan_before
    assert again.exit_code == 0, again.output
    assert "Already migrated" in again.output


@pytest.mark.parametrize(
    ("change", "reported"),
    [
        ({"arms.guided.digest": "0" * 64}, "execution.arms"),
        ({"versions.harbor": "0.22.0"}, "execution.versions"),
        ({"versions.tools": {"pytest": "8.4.1"}}, "execution.tools"),
        ({"spec.analysis.blocks": 3}, "execution.design"),
    ],
)
def test_a_run_of_another_experiment_is_refused(
    tmp_path: Path,
    experiment: tuple[Path, dict[str, Any]],
    change: dict[str, Any],
    reported: str,
) -> None:
    _, current = experiment
    assert current["execution"]["tools"] == {"scb-check": "1"}
    run_dir = _run(tmp_path, _version_one(current, **change))

    result = _migrate(tmp_path, run_dir)

    assert result.exit_code != 0
    assert reported in result.output
    assert "lock_migration" not in json.loads((run_dir / "run.json").read_text())


def test_unverifiable_inputs_are_refused(
    tmp_path: Path, experiment: tuple[Path, dict[str, Any]]
) -> None:
    _, current = experiment
    executed = _version_one(current)
    run_dir = _run(tmp_path, executed)
    manifest = run_dir / "run.json"
    original = manifest.read_text()

    manifest.write_text(original.replace(executed["lock_sha256"], "0" * 64))
    other_run = _migrate(tmp_path, run_dir)
    manifest.write_text(original)
    tampered = {**executed, "files": {}}
    (tmp_path / "executed.lock.json").write_text(json.dumps(tampered))
    edited = _migrate(tmp_path, run_dir)
    (tmp_path / "executed.lock.json").write_text(json.dumps(current))
    not_version_one = _migrate(tmp_path, run_dir)

    assert "was executed under lock" in other_run.output
    assert "does not match the lock body" in edited.output
    assert "only version 1 locks are migrated" in not_version_one.output
    assert all(r.exit_code != 0 for r in (other_run, edited, not_version_one))
