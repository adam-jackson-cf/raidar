"""Tests for CLI utility commands and helpers."""

import json
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


def _create_scaffold_files(task_dir: Path, version: str) -> None:
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


def test_task_clone_version_auto_increments(tmp_path: Path) -> None:
    runner = CliRunner()
    task_dir = tmp_path / "tasks" / "sample-task"

    init_result = runner.invoke(
        main,
        ["task", "init", "--path", str(task_dir), "--name", "sample-task"],
    )
    assert init_result.exit_code == 0, init_result.output

    _create_scaffold_files(task_dir, version="v001")

    clone_result = runner.invoke(
        main,
        ["task", "clone-version", "--path", str(task_dir), "--from-version", "v001"],
    )
    assert clone_result.exit_code == 0, clone_result.output
    assert "target_version: v002" in clone_result.output

    cloned_task_yaml = task_dir / "v002" / "task.yaml"
    cloned_task = TaskDefinition.from_yaml(cloned_task_yaml)
    assert cloned_task.version == "v002"
    assert (task_dir / "v002" / "scaffold" / "src" / "app" / "page.tsx").exists()


def test_task_clone_version_succeeds_without_scaffold_manifest(tmp_path: Path) -> None:
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
    assert clone_result.exit_code == 0, clone_result.output
    assert (task_dir / "v002").exists()


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


def _write_execution_summary(
    execution_dir: Path,
    *,
    task_name: str,
    model: str,
    harness: str,
    created_at: str,
    run_count_total: int = 1,
    void_count: int = 0,
) -> None:
    execution_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at_utc": created_at,
        "config": {
            "task_name": task_name,
            "harness": harness,
            "model": model,
        },
        "aggregate": {
            "run_count_total": run_count_total,
            "void_count": void_count,
        },
    }
    (execution_dir / "suite-summary.json").write_text(json.dumps(payload), encoding="utf-8")


def test_executions_list_filters_and_json_output(tmp_path: Path) -> None:
    runner = CliRunner()
    executions_root = tmp_path / "executions"
    _write_execution_summary(
        executions_root / "20260222-100000Z__hello-world-smoke__v001",
        task_name="hello-world-smoke",
        model="anthropic/claude-haiku-4-5",
        harness="claude-code",
        created_at="2026-02-22T10:00:00+00:00",
    )
    _write_execution_summary(
        executions_root / "20260222-110000Z__homepage-implementation__v001",
        task_name="homepage-implementation",
        model="codex/gpt-5.2-high",
        harness="codex-cli",
        created_at="2026-02-22T11:00:00+00:00",
    )

    text_result = runner.invoke(
        main,
        [
            "executions",
            "list",
            "--executions-root",
            str(executions_root),
            "--task",
            "homepage",
        ],
    )
    assert text_result.exit_code == 0, text_result.output
    assert "homepage-implementation@v001" in text_result.output
    assert "hello-world-smoke@v001" not in text_result.output

    json_result = runner.invoke(
        main,
        [
            "executions",
            "list",
            "--executions-root",
            str(executions_root),
            "--json",
        ],
    )
    assert json_result.exit_code == 0, json_result.output
    rows = json.loads(json_result.output)
    assert isinstance(rows, list)
    assert rows[0]["execution_id"] == "20260222-110000Z__homepage-implementation__v001"


def test_executions_prune_keeps_latest_per_model(tmp_path: Path) -> None:
    runner = CliRunner()
    executions_root = tmp_path / "executions"
    archive_root = tmp_path / "archive"

    old_dir = executions_root / "20260220-100000Z__hello-world-smoke__v001"
    new_dir = executions_root / "20260221-100000Z__hello-world-smoke__v001"
    other_model_dir = executions_root / "20260222-100000Z__hello-world-smoke__v001"
    _write_execution_summary(
        old_dir,
        task_name="hello-world-smoke",
        model="anthropic/claude-haiku-4-5",
        harness="claude-code",
        created_at="2026-02-20T10:00:00+00:00",
    )
    _write_execution_summary(
        new_dir,
        task_name="hello-world-smoke",
        model="anthropic/claude-haiku-4-5",
        harness="claude-code",
        created_at="2026-02-21T10:00:00+00:00",
    )
    _write_execution_summary(
        other_model_dir,
        task_name="hello-world-smoke",
        model="codex/gpt-5.2-high",
        harness="codex-cli",
        created_at="2026-02-22T10:00:00+00:00",
    )

    result = runner.invoke(
        main,
        [
            "executions",
            "prune",
            "--executions-root",
            str(executions_root),
            "--archive-dir",
            str(archive_root),
            "--keep-per-model",
            "1",
            "--no-include-legacy",
        ],
    )
    assert result.exit_code == 0, result.output
    assert new_dir.exists()
    assert other_model_dir.exists()
    assert not old_dir.exists()
    assert (archive_root / "executions" / old_dir.name).exists()
    assert "executions_pruned=1" in result.output


def test_executions_prune_dry_run_does_not_move_directories(tmp_path: Path) -> None:
    runner = CliRunner()
    executions_root = tmp_path / "executions"
    archive_root = tmp_path / "archive"
    old_dir = executions_root / "20260220-100000Z__hello-world-smoke__v001"
    new_dir = executions_root / "20260221-100000Z__hello-world-smoke__v001"
    _write_execution_summary(
        old_dir,
        task_name="hello-world-smoke",
        model="anthropic/claude-haiku-4-5",
        harness="claude-code",
        created_at="2026-02-20T10:00:00+00:00",
    )
    _write_execution_summary(
        new_dir,
        task_name="hello-world-smoke",
        model="anthropic/claude-haiku-4-5",
        harness="claude-code",
        created_at="2026-02-21T10:00:00+00:00",
    )

    result = runner.invoke(
        main,
        [
            "executions",
            "prune",
            "--executions-root",
            str(executions_root),
            "--archive-dir",
            str(archive_root),
            "--keep-per-model",
            "1",
            "--no-include-legacy",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert old_dir.exists()
    assert new_dir.exists()
    assert not archive_root.exists()
    assert "would-archive: executions/" in result.output
