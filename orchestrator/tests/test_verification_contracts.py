"""Verification plan contract and normalization tests."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from raidar.schemas.environment import CapabilityRequirements
from raidar.scorers.base import registered_scorers
from raidar.scorers.code_task.bugfix import Bugfix, BugfixV2
from raidar.scorers.code_task.refactor import Refactor, RefactorV2
from raidar.scorers.code_task.typescript import TypeScriptCodeTask
from raidar.scorers.design_to_code import DesignToCode
from raidar.scorers.plan_to_code import PlanToCode, PlanToCodeV2
from raidar.scorers.test_generation import TestGeneration, TestGenerationV2
from raidar.scorers.evidence_contract import (
    CommandEvidenceResult,
    EvidenceActionResult,
    EvidenceOutput,
    EvidenceRunnerMetadata,
    JsonEvidenceResult,
    PublicContractSnapshotEvidenceResult,
    StructuralSnapshotEvidenceResult,
)
from raidar.scorers.verification import (
    EvidenceDependency,
    EvidencePlanAction,
    VerificationAction,
    VerificationEvidence,
    VerificationPlan,
    VerificationPlanContext,
    VerificationPlanError,
    VisualComparisonToolPolicy,
    archive,
    command,
    glob,
    normalize_verification_plans,
    read_file,
    read_json,
    public_contract_snapshot,
    structural_snapshot,
    visual_diff,
)

import_module("raidar.scorers.scorer_registration")


def _visual_comparison_policy() -> VisualComparisonToolPolicy:
    return VisualComparisonToolPolicy(
        executable="compare-images",
        arguments_template=(
            "--reference",
            "{reference}",
            "--actual",
            "{actual}",
            "--diff",
            "{diff}",
            "--tolerance",
            "{threshold}",
        ),
        threshold=0.12,
        timeout_sec=17,
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
    assert normalized.payload()["runner_contract"] == "python-evidence-runner@2"
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

    with pytest.raises(VerificationPlanError, match="config keys mismatch"):
        normalize_verification_plans(
            (
                VerificationPlan(
                    owner="scorer",
                    actions=(
                        VerificationAction(
                            id="bad-command",
                            kind="command",
                            timeout_sec=60,
                            config={},
                        ),
                    ),
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

    with pytest.raises(VerificationPlanError, match="config keys mismatch"):
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


def test_normalize_verification_plan_deduplicates_alias_dependent_actions() -> None:
    normalized = normalize_verification_plans(
        (
            VerificationPlan(
                owner="one",
                actions=(
                    command("coverage", ["python", "-m", "coverage", "json"]),
                    read_json("report", "coverage.json", depends_on=("coverage",)),
                ),
            ),
            VerificationPlan(
                owner="two",
                actions=(
                    command("coverage", ["python", "-m", "coverage", "json"]),
                    read_json("report", "coverage.json", depends_on=("coverage",)),
                ),
            ),
        )
    )

    assert [action.id for action in normalized.actions] == ["one.coverage", "one.report"]
    assert normalized.aliases == {
        "one.coverage": "one.coverage",
        "one.report": "one.report",
        "two.coverage": "one.coverage",
        "two.report": "one.report",
    }
    assert normalized.actions[1].depends_on == ("one.coverage",)


def test_action_constructors_include_optional_config() -> None:
    actions = (
        read_file("text", "README.md", max_bytes=10),
        read_json("json", "payload.json", max_bytes=20),
        visual_diff(
            "visual",
            reference="ref.png",
            actual="actual.png",
            diff="diff.png",
            argv_template=["visual", "{reference}", "{actual}", "{diff}", "{threshold}"],
            threshold=0.03,
            timeout_sec=60,
        ),
        archive("archive", "artifacts"),
    )

    normalized = normalize_verification_plans((VerificationPlan(owner="owner", actions=actions),))

    payloads = {action["id"]: action for action in normalized.payload()["actions"]}
    assert payloads["owner.text"]["config"]["max_bytes"] == 10
    assert payloads["owner.json"]["config"]["max_bytes"] == 20
    assert payloads["owner.visual"]["config"]["regions"] == []
    assert payloads["owner.archive"]["config"] == {
        "path": "artifacts",
        "output": "archive.tar.gz",
    }


def test_snapshot_actions_require_unique_safe_selected_paths() -> None:
    normalized = normalize_verification_plans(
        (
            VerificationPlan(
                owner="owner",
                actions=(
                    structural_snapshot("structure", ("src/module.py",)),
                    public_contract_snapshot("contract", ("src/module.py",)),
                ),
            ),
        )
    )

    payloads = {action["id"]: action for action in normalized.payload()["actions"]}
    assert payloads["owner.structure"]["config"] == {"paths": ["src/module.py"]}
    assert payloads["owner.contract"]["config"] == {"paths": ["src/module.py"]}

    for paths, match in (
        ((), "non-empty list"),
        (("src/module.py", "src/module.py"), "must be unique"),
        (("../module.py",), "parent traversal"),
    ):
        with pytest.raises(VerificationPlanError, match=match):
            normalize_verification_plans(
                (
                    VerificationPlan(
                        owner="owner",
                        actions=(structural_snapshot("snapshot", paths),),
                    ),
                )
            )


def test_snapshot_evidence_models_are_kind_typed() -> None:
    structural = EvidenceActionResult(
        id="owner.structure",
        kind="structural_snapshot",
        required=True,
        status="ok",
        duration_sec=0.0,
        result={
            "digest": "a" * 64,
            "files": [
                {
                    "bytes": 1,
                    "code_line_count": 1,
                    "line_count": 1,
                    "nonempty_line_count": 1,
                    "path": "src/module.py",
                    "sha256": "b" * 64,
                }
            ],
        },
    )
    contract = EvidenceActionResult(
        id="owner.contract",
        kind="public_contract_snapshot",
        required=True,
        status="ok",
        duration_sec=0.0,
        result={
            "digest": "c" * 64,
            "files": [
                {
                    "declarations": ["def public_api(value: int) -> str"],
                    "path": "src/module.py",
                    "sha256": "b" * 64,
                }
            ],
        },
    )

    assert isinstance(structural.result, StructuralSnapshotEvidenceResult)
    assert isinstance(contract.result, PublicContractSnapshotEvidenceResult)


def test_normalize_verification_plan_rejects_unsatisfied_dependencies() -> None:
    with pytest.raises(VerificationPlanError, match="unsatisfied scorer evidence dependencies"):
        normalize_verification_plans(
            (VerificationPlan(owner="owner", actions=(glob("sources", "**/*.py"),)),),
            {"owner": (EvidenceDependency(kind="command"),)},
        )


def test_verification_evidence_resolves_owner_aliases() -> None:
    command_result = CommandEvidenceResult(
        command=["python", "-m", "pytest"],
        cwd=".",
        duration_sec=1.0,
        exit_code=0,
        stderr="",
        stdout="passed",
        timed_out=False,
    )
    output = EvidenceOutput(
        schema_version="1",
        runner_contract="python-evidence-runner@2",
        aliases={"one.pytest": "one.pytest", "two.tests": "one.pytest"},
        actions=[
            EvidenceActionResult(
                id="one.pytest",
                kind="command",
                required=True,
                status="ok",
                duration_sec=1.0,
                result=command_result,
            )
        ],
        artifacts=[],
        metadata=EvidenceRunnerMetadata(
            action_count=1,
            error_count=0,
            required_error_count=0,
            workspace="/app",
        ),
    )

    evidence = VerificationEvidence.from_output(output)

    assert evidence.for_owner("two").action("tests") is evidence.actions["one.pytest"]


def test_evidence_models_are_kind_typed_and_reject_coercion() -> None:
    action = EvidenceActionResult(
        id="owner.report",
        kind="read_json",
        required=True,
        status="ok",
        duration_sec=0.1,
        result={
            "bytes": 8,
            "content": "scalar",
            "path": "report.json",
            "sha256": "abc",
            "truncated": False,
        },
    )

    assert isinstance(action.result, JsonEvidenceResult)

    with pytest.raises(ValueError, match="unsuccessful evidence action requires error"):
        EvidenceActionResult(
            id="owner.skipped",
            kind="glob",
            required=False,
            status="skipped",
            duration_sec=0.0,
        )

    with pytest.raises(ValueError, match="timeout_sec"):
        EvidencePlanAction.model_validate(
            {
                "id": "owner.command",
                "kind": "command",
                "timeout_sec": "60",
                "required": True,
                "depends_on": [],
                "config": {"argv": ["python", "-V"]},
            }
        )


def test_every_execution_dependent_scorer_declares_a_satisfied_plan() -> None:
    scenario = SimpleNamespace(
        visual=None,
        verification=SimpleNamespace(
            gates=[],
            required_commands=[["python", "-m", "pytest"]],
            preflight_command_timeout_sec=30,
        ),
    )
    plans = []
    dependencies = {}
    for scorer_type in registered_scorers().values():
        if not scorer_type.evidence_dependencies:
            continue
        context = VerificationPlanContext(
            scenario=scenario,
            resolved_scorer=SimpleNamespace(metrics=()),
            scenario_dir=Path("."),
            environment_capabilities=CapabilityRequirements(),
            visual_comparison=_visual_comparison_policy(),
        )
        plan = scorer_type().verification_plan(context)
        plans.append(plan)
        dependencies[plan.owner] = scorer_type.evidence_dependencies

    normalized = normalize_verification_plans(tuple(plans), dependencies)

    assert dependencies
    assert set(dependencies) <= {alias.split(".", 1)[0] for alias in normalized.aliases}


def test_task_conditioned_v2_verification_plans_use_distinct_owners() -> None:
    scenario = SimpleNamespace(
        visual=None,
        verification=SimpleNamespace(
            gates=[],
            required_commands=[["python", "-m", "pytest"]],
            preflight_command_timeout_sec=30,
        ),
    )
    context = VerificationPlanContext(
        scenario=scenario,
        resolved_scorer=SimpleNamespace(metrics=()),
        scenario_dir=Path("."),
        environment_capabilities=CapabilityRequirements(),
        visual_comparison=_visual_comparison_policy(),
    )
    scorer_pairs = (
        (Bugfix(), BugfixV2()),
        (Refactor(), RefactorV2()),
        (TestGeneration(), TestGenerationV2()),
        (PlanToCode(), PlanToCodeV2()),
    )
    plans = tuple(
        plan
        for pair in scorer_pairs
        for plan in (pair[0].verification_plan(context), pair[1].verification_plan(context))
    )

    assert {plan.owner for plan in plans} == {
        "bugfix",
        "bugfix-v2",
        "refactor",
        "refactor-v2",
        "test-generation",
        "test-generation-v2",
        "plan-to-code",
        "plan-to-code-v2",
    }
    normalized = normalize_verification_plans(plans)
    assert {
        "bugfix-v2.required.0",
        "refactor-v2.required.0",
        "test-generation-v2.required.0",
        "plan-to-code-v2.required.0",
    } <= set(normalized.aliases)


def test_design_to_code_verification_plan_uses_visual_and_resolved_metric_config() -> None:
    class Region:
        name = "hero"
        reference_image = "reference-hero.png"
        actual_image = "actual-hero.png"
        diff_image = "diff-hero.png"
        weight = 1.0

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
                    "artifact_manifest": SimpleNamespace(
                        actual_image="actual.png",
                        diff_image="diff.png",
                    ),
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

    visual_comparison = _visual_comparison_policy()
    context = VerificationPlanContext(
        scenario=scenario,
        resolved_scorer=resolved_scorer,
        scenario_dir=Path("."),
        environment_capabilities=CapabilityRequirements(),
        visual_comparison=visual_comparison,
    )
    plan = DesignToCode().verification_plan(context)
    typescript_plan = TypeScriptCodeTask().verification_plan(context)

    action_ids = [action.id for action in plan.actions]
    assert "capture" in action_ids
    assert "visual.homepage" in action_ids
    assert "artifacts.required.0" in action_ids
    assert "artifacts.required.1" in action_ids
    for scorer_plan in (plan, typescript_plan):
        visual_action = next(
            action for action in scorer_plan.actions if action.kind == "visual_diff"
        )
        assert visual_action.config["argv_template"] == list(visual_comparison.argv_template)
        assert visual_action.config["threshold"] == 0.12
        assert visual_action.timeout_sec == 17


def test_visual_comparison_policy_rejects_incomplete_or_invalid_contracts() -> None:
    with pytest.raises(TypeError, match="executable"):
        VisualComparisonToolPolicy(
            executable="",
            arguments_template=("{reference}", "{actual}", "{diff}", "{threshold}"),
            threshold=0.03,
            timeout_sec=60,
        )
    with pytest.raises(TypeError, match="tuple of non-empty strings"):
        VisualComparisonToolPolicy(
            executable="compare-images",
            arguments_template=["{reference}", "{actual}", "{diff}", "{threshold}"],  # type: ignore[arg-type]
            threshold=0.03,
            timeout_sec=60,
        )
    with pytest.raises(ValueError, match="missing placeholders"):
        VisualComparisonToolPolicy(
            executable="compare-images",
            arguments_template=("{reference}", "{actual}", "{diff}"),
            threshold=0.03,
            timeout_sec=60,
        )
    with pytest.raises(ValueError, match="threshold"):
        VisualComparisonToolPolicy(
            executable="compare-images",
            arguments_template=("{reference}", "{actual}", "{diff}", "{threshold}"),
            threshold=1.1,
            timeout_sec=60,
        )
    with pytest.raises(TypeError, match="must be an integer"):
        VisualComparisonToolPolicy(
            executable="compare-images",
            arguments_template=("{reference}", "{actual}", "{diff}", "{threshold}"),
            threshold=0.03,
            timeout_sec=True,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="must be positive"):
        VisualComparisonToolPolicy(
            executable="compare-images",
            arguments_template=("{reference}", "{actual}", "{diff}", "{threshold}"),
            threshold=0.03,
            timeout_sec=0,
        )
