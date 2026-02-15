"""Tests for CLI utility commands and helpers."""

from pathlib import Path

from click.testing import CliRunner

from agentic_eval.cli import (
    RunCliOptions,
    _assert_no_generated_artifact_changes,
    _generated_artifact_paths,
    main,
)
from agentic_eval.schemas.task import TaskDefinition


def test_generated_artifact_paths_filters_prefixes() -> None:
    paths = [
        "orchestrator/results-smoke/runs/a/result.json",
        "orchestrator/workspace-smoke/src/app/page.tsx",
        "tasks/hello-world-smoke/task.yaml",
        "orchestrator/src/agentic_eval/cli.py",
    ]

    matches = _generated_artifact_paths(paths)

    assert matches == [
        "orchestrator/results-smoke/runs/a/result.json",
        "orchestrator/workspace-smoke/src/app/page.tsx",
    ]


def test_run_cli_options_resolved_caps_retry_and_resolves_paths(tmp_path: Path) -> None:
    options = RunCliOptions(
        task=tmp_path / "task.yaml",
        agent="gemini",
        model="google/gemini-3-flash-preview",
        rules="none",
        scaffolds_root=tmp_path / "scaffolds",
        workspace=tmp_path / "workspace",
        output=tmp_path / "results",
        timeout=300,
        repeats=5,
        repeat_parallel=2,
        retry_void=7,
    )

    resolved = options.resolved()

    assert resolved.retry_void == 1
    assert resolved.task.is_absolute()
    assert resolved.scaffolds_root.is_absolute()
    assert resolved.workspace.is_absolute()
    assert resolved.output.is_absolute()


def test_task_init_creates_schema_valid_task_and_rules(tmp_path: Path) -> None:
    runner = CliRunner()
    task_dir = tmp_path / "tasks" / "sample-task"

    result = runner.invoke(main, ["task", "init", "--path", str(task_dir), "--name", "sample-task"])

    assert result.exit_code == 0, result.output
    task_yaml = task_dir / "task.yaml"
    assert task_yaml.exists()

    task_def = TaskDefinition.from_yaml(task_yaml)
    assert task_def.name == "sample-task"
    assert task_def.verification.required_commands == [
        ["bun", "run", "typecheck"],
        ["bun", "run", "lint"],
    ]
    assert [gate.command for gate in task_def.verification.gates] == [
        ["bun", "run", "typecheck"],
        ["bun", "run", "lint"],
    ]

    for variant in ("strict", "minimal", "none"):
        variant_dir = task_dir / "rules" / variant
        assert (variant_dir / "AGENTS.md").exists()
        assert (variant_dir / "CLAUDE.md").exists()
        assert (variant_dir / "GEMINI.md").exists()


def test_artifact_guard_allows_generated_artifact_deletions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "agentic_eval.cli._changed_repo_entries",
        lambda _: [
            ("D", "orchestrator/results-smoke/runs/a/summary/result.json"),
            ("D", "orchestrator/workspace-smoke/src/app/page.tsx"),
        ],
    )

    _assert_no_generated_artifact_changes(tmp_path)
