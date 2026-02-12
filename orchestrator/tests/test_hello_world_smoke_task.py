"""Schema and rules validation for the hello-world smoke task."""

from pathlib import Path

from agentic_eval.schemas.task import TaskDefinition

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_DIR = REPO_ROOT / "tasks" / "hello-world-smoke"
TASK_PATH = TASK_DIR / "task.yaml"


def test_hello_world_smoke_task_loads() -> None:
    task = TaskDefinition.from_yaml(TASK_PATH)

    assert task.name == "hello-world-smoke"
    assert task.scaffold.template == "next-shadcn-starter"
    assert task.verification.required_commands == [
        ["bun", "run", "typecheck"],
        ["bun", "run", "lint"],
    ]
    assert task.verification.gates == []


def test_hello_world_smoke_rules_exist_for_supported_harnesses() -> None:
    expected_files = ["AGENTS.md", "CLAUDE.md", "GEMINI.md"]
    variants = ["strict", "minimal", "none"]

    for variant in variants:
        variant_dir = TASK_DIR / "rules" / variant
        assert variant_dir.is_dir()
        for filename in expected_files:
            assert (variant_dir / filename).is_file()
