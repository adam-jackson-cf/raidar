"""Schema, evidence-contract, and rules validation for the bugfix ledger scenario."""

from pathlib import Path

from raidar.schemas.scenario import ScenarioDefinition

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = REPO_ROOT / "scenarios" / "bugfix-ledger-balance" / "v001"
SCENARIO_PATH = SCENARIO_DIR / "scenario.yaml"


def test_bugfix_ledger_scenario_loads() -> None:
    scenario = ScenarioDefinition.from_yaml(SCENARIO_PATH)

    assert scenario.name == "bugfix-ledger-balance"
    assert scenario.scenario_revision == "v001"
    assert scenario.category == "bugfix"
    assert scenario.scorer_ids() == ["bugfix@1", "requirements@1", "resource-efficiency@1"]
    assert scenario.metric_ids() == [
        "defect-resolution",
        "regression-protection",
        "change-containment",
        "verification-stability",
        "defect-evidence-completeness",
        "requirements-coverage",
        "requirements-adherence",
        "resource-efficiency",
    ]
    assert scenario.verification.required_commands == [
        ["bun", "run", "typecheck"],
        ["bun", "run", "lint"],
        ["bun", "run", "test"],
        ["bun", "run", "test:coverage"],
    ]
    assert scenario.verification.coverage_threshold == 0.8


def test_bugfix_ledger_scenario_declares_defect_evidence_contract() -> None:
    scenario = ScenarioDefinition.from_yaml(SCENARIO_PATH)

    assert [entry.path for entry in scenario.evidence.retained_files] == [
        "evidence/defect-evidence.json"
    ]
    requirement_ids = [item.id for item in scenario.requirements.items]
    assert "req-repro-test-enabled" in requirement_ids
    assert "req-debit-regression-suite" in requirement_ids
    assert "req-defect-evidence-retained" in requirement_ids


def test_bugfix_ledger_starter_ships_parked_reproduction_test() -> None:
    test_source = (SCENARIO_DIR / "starter" / "src" / "test" / "ledger.test.ts").read_text(
        encoding="utf-8"
    )
    ledger_source = (SCENARIO_DIR / "starter" / "src" / "lib" / "ledger.ts").read_text(
        encoding="utf-8"
    )

    assert 'it.skip("subtracts debit entries from the balance"' in test_source
    assert "balance += entry.amountCents;" in ledger_source


def test_bugfix_ledger_rules_exist_for_supported_agents() -> None:
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
