"""Construct one checkpoint's enabled stages and their ordering requirements."""

from __future__ import annotations

from typing import Any

from raidar.subject.workflow.define import Define
from raidar.subject.workflow.definition import WorkflowDefinition
from raidar.subject.workflow.stage import Build, Stage
from raidar.subject.workflow.verify import Verify


def workflow_stages(definition: WorkflowDefinition) -> list[Stage]:
    stages: list[Stage] = []
    prerequisites: list[dict[str, Any]] = []
    define = definition.stages.define
    if define is not None and define.enabled:
        defining = Define(define)
        stages.append(defining)
        prerequisites.append({"name": "define.unsealed", "argv": defining.paths.inspector})
    stages.append(Build(definition.stages.build))
    verify = definition.stages.verify
    if verify is not None and verify.enabled:
        stages.append(Verify(verify, prerequisites=prerequisites))
    return stages
