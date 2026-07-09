"""Verification plan contract and normalization tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.schemas.environment import CapabilityRequirements
from raidar.scorers.design_to_code import DesignToCode
from raidar.scorers.verification import (
    VerificationAction,
    VerificationPlan,
    VerificationPlanContext,
    VerificationPlanError,
    archive,
    command,
    glob,
    normalize_verification_plans,
    read_file,
    read_json,
    visual_diff,
)


def test_normalize_verification_plan_namespaces_and_orders_dependencies() -> None:
    normalized = normalize_verification_plans(
        (
            VerificationPlan(
                owner="python-code-task",
                actions=(
                    read_json("coverage.report", ".raidar/coverage.json", depends_on=("coverage",)),
                    command("coverage", ["python", "-m", "coverage", "json"]),
                    glob("sources", "**/*.py"),
                ),
            ),
        )
    )

    assert [action.id for action in normalized.actions] == [
        "python-code-task.coverage",
        "python-code-task.coverage.report",
        "python-code-task.sources",
    ]
    assert normalized.aliases == {
        "python-code-task.coverage": "python-code-task.coverage",
        "python-code-task.coverage.report": "python-code-task.coverage.report",
        "python-code-task.sources": "python-code-task.sources",
    }
    assert normalized.payload()["runner_contract"] == "python-evidence-runner@1"
    assert len(normalized.hash()) == 64


def test_normalize_verification_plan_rejects_shell_commands_and_bad_paths() -> None:
    with pytest.raises(VerificationPlanError, match="verification plan owner"):
        normalize_verification_plans(
            (
                VerificationPlan(
                    owner="bad owner",
                    actions=(glob("sources", "**/*.py"),),
                ),
            )
        )

    with pytest.raises(VerificationPlanError, match="shell wrapper"):
        normalize_verification_plans(
            (
                VerificationPlan(
                    owner="scorer",
                    actions=(command("bad", ["bash", "-lc", "pytest"]),),
                ),
            )
        )

    with pytest.raises(VerificationPlanError, match="command argv is required"):
        normalize_verification_plans(
            (
                VerificationPlan(
                    owner="scorer",
                    actions=(VerificationAction(id="bad-command", kind="command", config={}),),
                ),
            )
        )

    with pytest.raises(VerificationPlanError, match="unsupported verification action kind"):
        normalize_verification_plans(
            (
                VerificationPlan(
                    owner="scorer",
                    actions=(
                        VerificationAction(
                            id="bad",
                            kind="scorecard",
                            config={},
                        ),
                    ),
                ),
            )
        )

    with pytest.raises(VerificationPlanError, match="timeout_sec must be positive"):
        normalize_verification_plans(
            (
                VerificationPlan(
                    owner="scorer",
                    actions=(
                        VerificationAction(
                            id="slow",
                            kind="glob",
                            timeout_sec=0,
                            config={"pattern": "**/*.py"},
                        ),
                    ),
                ),
            )
        )

    with pytest.raises(VerificationPlanError, match="parent traversal"):
        normalize_verification_plans(
            (
                VerificationPlan(
                    owner="scorer",
                    actions=(read_json("bad", "../coverage.json"),),
                ),
            )
        )

    with pytest.raises(VerificationPlanError, match="config.path is required"):
        normalize_verification_plans(
            (
                VerificationPlan(
                    owner="scorer",
                    actions=(
                        VerificationAction(
                            id="missing",
                            kind="read_json",
                            config={},
                        ),
                    ),
                ),
            )
        )


def test_normalize_verification_plan_rejects_duplicate_ids_unknown_dependencies_and_cycles() -> (
    None
):
    with pytest.raises(VerificationPlanError, match="duplicate action ids"):
        normalize_verification_plans(
            (
                VerificationPlan(
                    owner="scorer",
                    actions=(
                        glob("sources", "**/*.py"),
                        glob("sources", "**/*.py"),
                    ),
                ),
            )
        )

    with pytest.raises(VerificationPlanError, match="depends on unknown actions"):
        normalize_verification_plans(
            (
                VerificationPlan(
                    owner="scorer",
                    actions=(glob("sources", "**/*.py", depends_on=("missing",)),),
                ),
            )
        )

    with pytest.raises(VerificationPlanError, match="dependency cycle"):
        normalize_verification_plans(
            (
                VerificationPlan(
                    owner="scorer",
                    actions=(
                        VerificationAction(
                            id="one",
                            kind="glob",
                            depends_on=("two",),
                            config={"pattern": "**/*.py"},
                        ),
                        VerificationAction(
                            id="two",
                            kind="glob",
                            depends_on=("one",),
                            config={"pattern": "**/*.py"},
                        ),
                    ),
                ),
            )
        )


def test_normalize_verification_plan_deduplicates_identical_actions_with_aliases() -> None:
    normalized = normalize_verification_plans(
        (
            VerificationPlan(owner="one", actions=(command("pytest", ["python", "-m", "pytest"]),)),
            VerificationPlan(owner="two", actions=(command("test", ["python", "-m", "pytest"]),)),
        )
    )

    assert [action.id for action in normalized.actions] == ["one.pytest"]
    assert normalized.aliases == {
        "one.pytest": "one.pytest",
        "two.test": "one.pytest",
    }


def test_action_constructors_include_optional_config() -> None:
    actions = (
        read_file("text", "README.md", max_bytes=10),
        read_json("json", "payload.json", max_bytes=20),
        visual_diff("visual", reference="ref.png", actual="actual.png", diff="diff.png"),
        archive("archive", "artifacts"),
    )

    normalized = normalize_verification_plans((VerificationPlan(owner="owner", actions=actions),))

    payloads = {action["id"]: action for action in normalized.payload()["actions"]}
    assert payloads["owner.text"]["config"]["max_bytes"] == 10
    assert payloads["owner.json"]["config"]["max_bytes"] == 20
    assert payloads["owner.visual"]["config"]["regions"] == []
    assert payloads["owner.archive"]["config"]["path"] == "artifacts"


def test_design_to_code_verification_plan_uses_visual_and_resolved_metric_config() -> None:
    class Region:
        def model_dump(self, *, mode: str):
            assert mode == "json"
            return {"name": "hero"}

    scenario = type(
        "Scenario",
        (),
        {
            "visual": type(
                "Visual",
                (),
                {
                    "reference_image": "reference.png",
                    "screenshot_command": ["bun", "run", "screenshot"],
                    "artifacts": {"actual": "actual.png", "diff": "diff.png"},
                    "regions": [Region()],
                },
            )()
        },
    )()
    resolved_scorer = type(
        "Resolved",
        (),
        {
            "metrics": (
                type(
                    "Metric",
                    (),
                    {
                        "id": "artifact-checks",
                        "config": {"required_paths": ["src/app/page.tsx", "src/components/*.tsx"]},
                    },
                )(),
            )
        },
    )()

    plan = DesignToCode().verification_plan(
        VerificationPlanContext(
            scenario=scenario,
            resolved_scorer=resolved_scorer,
            scenario_dir=Path("."),
            environment_capabilities=CapabilityRequirements(),
        )
    )

    action_ids = [action.id for action in plan.actions]
    assert "capture" in action_ids
    assert "visual.homepage" in action_ids
    assert "artifacts.required.0" in action_ids
    assert "artifacts.required.1" in action_ids
