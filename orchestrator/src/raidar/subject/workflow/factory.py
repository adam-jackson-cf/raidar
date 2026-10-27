"""Construct one checkpoint's enabled stages and their ordering requirements."""

from __future__ import annotations

from typing import Any

from raidar.subject.workflow import verify
from raidar.subject.workflow.acceptance import Acceptance
from raidar.subject.workflow.build import Build
from raidar.subject.workflow.define import Define
from raidar.subject.workflow.definition import WorkflowDefinition
from raidar.subject.workflow.stage import Stage


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
