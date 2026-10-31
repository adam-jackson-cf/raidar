"""Schema-2 judge admission: reply, criteria producers, pytest evidence and bounds."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from raidar_contract.package.registry import load_stage_package
from raidar_contract.releases import Release
from raidar_contract.workflow import PackageJudge, PackageWorkflowDefinition

from raidar.subject.primitive.site import TrialInvalid
from raidar.subject.workflow.package_checks import ResolvedStage, package_issues
from raidar.subject.workflow.package_stage import PackageStage
from raidar.subject.workflow.stage import Checkpoint
from tests.package_support import write_package
from tests.test_primitive_gate import Container


def judge_document(**changes: Any) -> dict[str, Any]:
    return {
        "criteria": "maker.criteria",
        "model": "typesafe/jev-1.13-20260917",
        "closed_at": 0.8,
        "open_at": 0.3,
        "max_passes": 3,
        "rework": "%%%PUSHBACK%%%\n%%%GATE%%%",
        **changes,
    }


def stage(
    tmp_path: Path, *, output: str | None = "criteria/1", pytest_count: int = 1
) -> ResolvedStage:
    manifest: dict[str, Any] = {"name": "maker", "requires": {"gate": "gate/1"}}
    manifest["gate"] = [{"name": f"tests-{n}", "kind": "pytest"} for n in range(pytest_count)]
    manifest["requires"]["outputs"] = "outputs/1"
    manifest["outputs"] = {"criteria": {"path": "/app/.raidar/maker/criteria.json"}}
    if output is not None:
        manifest["outputs"]["criteria"]["contract"] = output
    package = load_stage_package(write_package(tmp_path / "acme", manifest))
    return ResolvedStage(Release("acme/maker", 1), package, {})


def test_judge_requires_structured_build_reply() -> None:
    with pytest.raises(ValidationError, match="ST3:"):
        PackageWorkflowDefinition.model_validate(
            {
                "schema": 2,
                "name": "judged",
                "build": {"prompt": "%%%TASK%%%"},
                "judge": judge_document(),
            }
        )


@pytest.mark.parametrize("criteria", ["absent.criteria", "maker.absent"])
def test_judge_refuses_missing_criteria_producer(tmp_path: Path, criteria: str) -> None:
    issues = package_issues(
        "%%%MAKER%%% %%%TASK%%%",
        [stage(tmp_path)],
        judge=PackageJudge.model_validate(judge_document(criteria=criteria)),
    )
    assert any(issue.startswith("ST3:") and criteria in issue for issue in issues)


def test_judge_refuses_unstructured_output(tmp_path: Path) -> None:
    producer = stage(tmp_path, output=None)
    issues = package_issues(
        "%%%MAKER%%% %%%TASK%%%",
        [producer],
        judge=PackageJudge.model_validate(judge_document()),
    )
    assert any(issue.startswith("ST3:") and "criteria/1" in issue for issue in issues)


@pytest.mark.parametrize("pytest_count", [0, 2])
def test_judge_refuses_missing_or_ambiguous_test_evidence(
    tmp_path: Path, pytest_count: int
) -> None:
    issues = package_issues(
        "%%%MAKER%%% %%%TASK%%%",
        [stage(tmp_path, pytest_count=pytest_count)],
        judge=PackageJudge.model_validate(judge_document()),
    )
    assert any(issue.startswith("ST3:") and "pytest" in issue for issue in issues)


def test_judge_accepts_declared_criteria_and_one_test_gate(tmp_path: Path) -> None:
    assert (
        package_issues(
            "%%%MAKER%%% %%%TASK%%%",
            [stage(tmp_path)],
            judge=PackageJudge.model_validate(judge_document()),
        )
        == []
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"open_at": 0.8},
        {"closed_at": 0.2},
        {"open_at": -0.1},
        {"closed_at": 1.1},
        {"max_passes": 0},
        {"rework": "%%%GATE%%%"},
        {"rework": "%%%PUSHBACK%%% %%%GATE%%% %%%GATE%%%"},
        {"rework": "%%%PUSHBACK%%% %%%GATE%%% %%%UNKNOWN%%%"},
        {"criteria": "criteria"},
        {"retries": 0},
        {"timeout_s": 999},
        {"question_set": "other/1"},
    ],
)
def test_judge_refuses_invalid_bounds_inputs_and_policy_overrides(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PackageJudge.model_validate(judge_document(**changes))


async def test_deferred_reply_execution_refuses_before_starting_tools(tmp_path: Path) -> None:
    definition = PackageWorkflowDefinition.model_validate(
        {
            "schema": 2,
            "name": "structured",
            "build": {"prompt": "%%%TASK%%%", "reply": "acceptance-reply/1"},
        }
    )
    runtime = PackageStage(
        definition, locked={}, primitives={}, pack="python", skills_placement=".agents/skills"
    )

    with pytest.raises(TrialInvalid) as refused:
        await runtime.prepare(Checkpoint(Container({}), tmp_path, "task"))

    assert refused.value.kind == "instrument"
    assert "acceptance-reply/1" in refused.value.detail
