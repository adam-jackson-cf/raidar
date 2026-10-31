"""``raidar workflow new``: start a workflow definition from an existing one.

The new workflow ``<ns>/<name>`` is a copy of ``lab/workflows/<from>/`` (default
``raidar/one-shot-verify``) under its own name; its manifest ``name`` is the leaf. Nothing is
released until its register line is added (``make releases-check`` prints it). An existing
folder is never overwritten.
"""

from __future__ import annotations

import re
import shutil

import click
import yaml

from raidar.contract.paths import workflows_root

DEFAULT_TEMPLATE = "raidar/one-shot-verify"
WORKFLOW_FILE = "workflow.yaml"
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*$")


def register(main: click.Group) -> None:
    main.add_command(workflow)


@click.group()
def workflow() -> None:
    """Workflow definitions: new."""


def _name(value: str, hint: str) -> str:
    if not _NAME.fullmatch(value):
        raise click.BadParameter(
            f"{value!r} is not a workflow name: <ns>/<name> of lowercase letters, digits, '-'",
            param_hint=hint,
        )
    return value


@workflow.command("new")
@click.argument("name")
@click.option(
    "--from",
    "template",
    default=DEFAULT_TEMPLATE,
    show_default=True,
    help="Existing workflow under lab/workflows/ to copy.",
)
def workflow_new(name: str, template: str) -> None:
    """Write the workflow NAME under lab/workflows/ as a copy of the template."""
    target = workflows_root() / _name(name, "NAME")
    source = workflows_root() / _name(template, "--from")
    if not (source / WORKFLOW_FILE).is_file():
        raise click.ClickException(f"no workflow template at {source}")
    if target.exists():
        raise click.ClickException(f"refusing to overwrite {target}")
    shutil.copytree(source, target, symlinks=False)
    path = target / WORKFLOW_FILE
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or "name" not in document:
        shutil.rmtree(target)
        raise click.ClickException(f"{source / WORKFLOW_FILE} is not a workflow definition")
    leaf = name.rsplit("/", 1)[-1]
    renamed = re.sub(r"(?m)^name:.*$", f"name: {leaf}", path.read_text(encoding="utf-8"), count=1)
    path.write_text(renamed, encoding="utf-8")
    click.echo(f"Wrote {target} (from {template})")
    click.echo("Next: edit it, then make releases-check prints the register line to add")
