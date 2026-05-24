"""Schema and rules validation for the hello-world smoke scenario."""

from pathlib import Path

from raidar.schemas.scenario import ScenarioDefinition

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = REPO_ROOT / "scenarios" / "hello-world-smoke" / "v001"
SCENARIO_PATH = SCENARIO_DIR / "scenario.yaml"


def test_hello_world_smoke_scenario_loads() -> None:
    scenario = ScenarioDefinition.from_yaml(SCENARIO_PATH)

    assert scenario.name == "hello-world-smoke"
    assert scenario.scenario_revision == "v001"
    assert scenario.starter.root == "starter"
    assert scenario.prompt.entry == "prompt/task.md"
    assert scenario.verification.required_commands == [
        ["bun", "run", "typecheck"],
        ["bun", "run", "lint"],
    ]
    assert scenario.verification.gates == []
    assert scenario.scorer_ids() == ["typescript-code-task@1", "resource-efficiency@1"]
    assert scenario.metric_ids() == [
        "functional",
        "code-quality",
        "test-coverage",
        "artifact-checks",
        "verification-stability",
        "resource-efficiency",
    ]


def test_hello_world_smoke_rules_exist_for_supported_agents() -> None:
    expected_files = [
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
        "copilot-instructions.md",
        "user-rules-setting.md",
    ]
    rules_dir = SCENARIO_DIR / "rules"
    assert rules_dir.is_dir()
    for filename in expected_files:
        assert (rules_dir / filename).is_file()
