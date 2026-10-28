"""Load a workflow of either schema: ``schema: 1`` (the spike's stage types) or ``schema: 2``
(released packages, ``package_definition``). The phase-5 port retires schema 1."""

from __future__ import annotations

from pathlib import Path

import yaml

from raidar.subject.workflow.definition import (
    WORKFLOW_FILE,
    WorkflowDefinition,
    WorkflowDefinitionError,
    load_workflow,
    workflow_dir,
)
from raidar.subject.workflow.package_definition import (
    PACKAGE_SCHEMA,
    PackageWorkflowDefinition,
    PackageWorkflowError,
    load_package_workflow,
)

AnyWorkflow = WorkflowDefinition | PackageWorkflowDefinition
WORKFLOW_ERRORS = (WorkflowDefinitionError, PackageWorkflowError)


def load_any_workflow(root: Path, name: str) -> AnyWorkflow:
    """The workflow ``<root>/<name>``, parsed by its ``schema``."""
    folder = workflow_dir(root, name)
    path = folder / WORKFLOW_FILE
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise WorkflowDefinitionError(f"{path}: unreadable workflow definition: {exc}") from exc
    if isinstance(document, dict) and document.get("schema") == PACKAGE_SCHEMA:
        return load_package_workflow(folder, name)
    return load_workflow(root, name)
