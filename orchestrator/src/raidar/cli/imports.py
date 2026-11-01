"""Local stage/workflow import commands; SOURCE is recorded, never verified."""

from __future__ import annotations

from pathlib import Path

import click
from raidar_contract.package import StagePackageError
from raidar_contract.references import ReleaseError
from raidar_contract.workflow import PackageWorkflowError

from raidar.cli import workflow_new
from raidar.contract.paths import REPO_ROOT
from raidar.contract.releases import ReleaseKind
from raidar.subject.imports import PackageImportError, import_folder


def register(main: click.Group, stage: click.Group) -> None:
    stage.add_command(command(ReleaseKind.STAGE))
    workflow_new.workflow.add_command(command(ReleaseKind.WORKFLOW))
    workflow_new.register(main)


def command(kind: ReleaseKind) -> click.Command:
    @click.command("import")
    @click.argument("name")
    @click.option("--src", required=True, type=click.Path(path_type=Path, file_okay=False))
    @click.option("--sha256", required=True)
    @click.option("--source", required=True)
    def import_command(name: str, src: Path, sha256: str, source: str) -> None:
        """Import an exported folder without changing its bytes or registering a release."""
        try:
            target, release = import_folder(kind, src, name, sha256, source, REPO_ROOT)
        except (
            PackageImportError,
            StagePackageError,
            PackageWorkflowError,
            ReleaseError,
            OSError,
        ) as error:
            raise click.ClickException(str(error)) from error
        click.echo(f"Imported {target}; provenance is unverified")
        click.echo(f"Register under {kind.value}: {release}")

    return import_command
