"""Tests for CLI utility commands and helpers."""

import json
from pathlib import Path

from click.testing import CliRunner

from agentic_eval.audit.scaffold_manifest import load_manifest
from agentic_eval.cli import (
    RunCliOptions,
    _assert_no_generated_artifact_changes,
    _generated_artifact_paths,
    main,
)
from agentic_eval.schemas.task import TaskDefinition


def test_generated_artifact_paths_filters_prefixes() -> None:
    paths = [
        "executions/20260220-000000Z__hello-world-smoke__v001/runs/run-01/run.json",
        "tasks/hello-world-smoke/v001/task.yaml",
        "orchestrator/src/agentic_eval/cli.py",
    ]

    matches = _generated_artifact_paths(paths)

    assert matches == [
        "executions/20260220-000000Z__hello-world-smoke__v001/runs/run-01/run.json",
    ]


def test_run_cli_options_resolved_caps_retry_and_resolves_paths(tmp_path: Path) -> None:
    options = RunCliOptions(
        task=tmp_path / "task.yaml",
        agent="gemini",
        model="google/gemini-3-flash-preview",
        timeout=300,
        repeats=5,
        repeat_parallel=2,
        retry_void=7,
    )

    resolved = options.resolved()

    assert resolved.retry_void == 1
    assert resolved.task.is_absolute()


def test_task_init_creates_schema_valid_task_and_rules(tmp_path: Path) -> None:
    runner = CliRunner()
    task_dir = tmp_path / "tasks" / "sample-task"

    result = runner.invoke(main, ["task", "init", "--path", str(task_dir), "--name", "sample-task"])

    assert result.exit_code == 0, result.output
    task_yaml = task_dir / "v001" / "task.yaml"
    assert task_yaml.exists()

    task_def = TaskDefinition.from_yaml(task_yaml)
    assert task_def.name == "sample-task"
    assert task_def.version == "v001"
    assert task_def.scaffold.root == "scaffold"
    assert task_def.prompt.entry == "prompt/task.md"
    assert task_def.verification.required_commands == [
        ["bun", "run", "typecheck"],
        ["bun", "run", "lint"],
    ]
    assert [gate.command for gate in task_def.verification.gates] == [
        ["bun", "run", "typecheck"],
        ["bun", "run", "lint"],
    ]

    rules_dir = task_dir / "v001" / "rules"
    assert (rules_dir / "AGENTS.md").exists()
    assert (rules_dir / "CLAUDE.md").exists()
    assert (rules_dir / "GEMINI.md").exists()
    assert (rules_dir / "copilot-instructions.md").exists()
    assert (rules_dir / "user-rules-setting.md").exists()
    assert (task_dir / "v001" / "prompt" / "task.md").exists()


def test_artifact_guard_allows_generated_artifact_deletions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "agentic_eval.cli._changed_repo_entries",
        lambda _: [
            (
                "D",
                "executions/20260220-000000Z__hello-world-smoke__v001/runs/run-01/run.json",
            ),
        ],
    )

    _assert_no_generated_artifact_changes(tmp_path)


def _create_scaffold_manifest(task_dir: Path, template: str, version: str) -> Path:
    scaffold_dir = task_dir / version / "scaffold"
    src_dir = scaffold_dir / "src" / "app"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "page.tsx").write_text("export default function Page() { return null; }\n")
    (scaffold_dir / "package.json").write_text(
        json.dumps({"dependencies": {}, "devDependencies": {}})
    )
    (scaffold_dir / "tsconfig.json").write_text("{}\n")
    (scaffold_dir / "next.config.ts").write_text("export default {};\n")
    (scaffold_dir / "postcss.config.mjs").write_text("export default {};\n")
    manifest_path = scaffold_dir / "scaffold.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-02-21T00:00:00+00:00",
                "version": "1.0.0",
                "template": template,
                "template_version": version,
                "fingerprint": "sha256:test",
                "files": {},
                "dependencies": {},
                "dev_dependencies": {},
                "quality_gates": {
                    "typecheck": "bun run typecheck",
                    "lint": "bunx ultracite check src",
                    "test": "bun test",
                },
                "pre_commit_hooks": ["typecheck", "lint"],
            },
            indent=2,
        )
    )
    return manifest_path


def test_task_clone_version_auto_increments_and_updates_manifest(tmp_path: Path) -> None:
    runner = CliRunner()
    task_dir = tmp_path / "tasks" / "sample-task"

    init_result = runner.invoke(
        main,
        ["task", "init", "--path", str(task_dir), "--name", "sample-task"],
    )
    assert init_result.exit_code == 0, init_result.output

    _create_scaffold_manifest(task_dir, template="sample-task", version="v001")

    clone_result = runner.invoke(
        main,
        ["task", "clone-version", "--path", str(task_dir), "--from-version", "v001"],
    )
    assert clone_result.exit_code == 0, clone_result.output
    assert "target_version: v002" in clone_result.output

    cloned_task_yaml = task_dir / "v002" / "task.yaml"
    cloned_task = TaskDefinition.from_yaml(cloned_task_yaml)
    assert cloned_task.version == "v002"

    cloned_manifest = load_manifest(task_dir / "v002" / "scaffold" / "scaffold.manifest.json")
    assert cloned_manifest.template == "sample-task"
    assert cloned_manifest.template_version == "v002"


def test_task_clone_version_fails_without_scaffold_manifest(tmp_path: Path) -> None:
    runner = CliRunner()
    task_dir = tmp_path / "tasks" / "sample-task"

    init_result = runner.invoke(
        main,
        ["task", "init", "--path", str(task_dir), "--name", "sample-task"],
    )
    assert init_result.exit_code == 0, init_result.output

    (task_dir / "v001" / "scaffold").mkdir(parents=True, exist_ok=True)

    clone_result = runner.invoke(
        main,
        ["task", "clone-version", "--path", str(task_dir), "--from-version", "v001"],
    )
    assert clone_result.exit_code != 0
    assert "Task scaffold manifest not found" in clone_result.output
    assert not (task_dir / "v002").exists()


def test_info_selects_latest_task_version_numerically(tmp_path: Path) -> None:
    runner = CliRunner()
    task_dir = tmp_path / "tasks" / "sample-task"

    init_v2 = runner.invoke(
        main,
        [
            "task",
            "init",
            "--path",
            str(task_dir),
            "--name",
            "sample-task",
            "--task-version",
            "v2",
        ],
    )
    assert init_v2.exit_code == 0, init_v2.output

    init_v10 = runner.invoke(
        main,
        [
            "task",
            "init",
            "--path",
            str(task_dir),
            "--name",
            "sample-task",
            "--task-version",
            "v10",
        ],
    )
    assert init_v10.exit_code == 0, init_v10.output

    info_result = runner.invoke(main, ["info", "--task", str(task_dir)])
    assert info_result.exit_code == 0, info_result.output
    assert "Version: v10" in info_result.output
