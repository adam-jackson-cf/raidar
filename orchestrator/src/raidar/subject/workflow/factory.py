"""Construct one checkpoint's enabled stages and their ordering requirements."""

from __future__ import annotations

import hashlib
from typing import Any

from raidar_contract.workflow import PackageWorkflowDefinition

from raidar.subject.primitive import calls
from raidar.subject.workflow import verify
from raidar.subject.workflow.acceptance import Acceptance
from raidar.subject.workflow.build import Build
from raidar.subject.workflow.components import skill_files
from raidar.subject.workflow.define import Define
from raidar.subject.workflow.definition import WorkflowDefinition
from raidar.subject.workflow.package_stage import PackageStage
from raidar.subject.workflow.stage import Checkpoint, Stage


def workflow_stages(definition: WorkflowDefinition) -> list[Stage]:
    stages: list[Stage] = []
    prerequisites: list[dict[str, Any]] = []
    acceptance = Acceptance()
    define = definition.stages.define
    if define is not None and define.enabled:
        defining = Define(define, acceptance)
        stages.append(defining)
        prerequisites.append(
            {
                "name": "define.unsealed",
                "argv": defining.paths.prerequisite,
                "watch": defining.paths.state,
                "writes": [],
            }
        )
    stages.append(Build(definition.stages.build, acceptance, verify.PATHS.workspace))
    gate = definition.stages.verify
    if gate is not None and gate.enabled:
        stages.append(verify.Verify(gate, prerequisites=prerequisites, acceptance=acceptance))
    return stages


def checkpoint_stages(
    definition: WorkflowDefinition | PackageWorkflowDefinition,
    *,
    locked: dict[str, dict[str, Any]],
    primitives: dict[str, str],
    pack: str,
    skills_placement: str,
) -> tuple[list[Stage], PackageStage | None]:
    """Select the generic package runtime or the preserved spike's stage classes."""
    if isinstance(definition, PackageWorkflowDefinition):
        package = PackageStage(
            definition,
            locked=locked,
            primitives=primitives,
            pack=pack,
            skills_placement=skills_placement,
        )
        return [package], package
    return workflow_stages(definition), None


async def place_components(
    checkpoint: Checkpoint,
    definition: WorkflowDefinition | PackageWorkflowDefinition,
    placement: str,
    components: list[dict[str, str]],
) -> None:
    """Place spike workflow skills; package skills belong to their ``install/1`` runtime."""
    if isinstance(definition, PackageWorkflowDefinition):
        return
    for relative, text in skill_files(definition, placement).items():
        record = f"workflow/components/{relative}"
        path = checkpoint.logs_dir / record
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        await calls.upload_file(checkpoint.environment, path, f"/app/{relative}")
        components.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "record": record,
            }
        )
