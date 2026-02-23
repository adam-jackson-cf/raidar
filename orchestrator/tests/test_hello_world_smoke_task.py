"""Schema and rules validation for the hello-world smoke task."""

from pathlib import Path

from raidar.schemas.task import TaskDefinition

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_DIR = REPO_ROOT / "tasks" / "hello-world-smoke" / "v001"
TASK_PATH = TASK_DIR / "task.yaml"


def test_hello_world_smoke_task_loads() -> None:
    task = TaskDefinition.from_yaml(TASK_PATH)

    assert task.name == "hello-world-smoke"
    assert task.version == "v001"
    assert task.scaffold.root == "scaffold"
    assert task.prompt.entry == "prompt/task.md"
    assert task.verification.required_commands == [
        ["bun", "run", "typecheck"],
        ["bun", "run", "lint"],
    ]
    assert task.verification.gates == []


def test_hello_world_smoke_rules_exist_for_supported_harnesses() -> None:
    expected_files = [
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
        "copilot-instructions.md",
        "user-rules-setting.md",
    ]
    rules_dir = TASK_DIR / "rules"
    assert rules_dir.is_dir()
    for filename in expected_files:
        assert (rules_dir / filename).is_file()
