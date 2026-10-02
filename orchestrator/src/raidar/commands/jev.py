"""Jev CLI commands: shadow runs, policies, labels (direct and blind) and promotion."""

from __future__ import annotations

import json
import re
from pathlib import Path

import click

from raidar.commands import jev_calibration
from raidar.jev.audit import AuditLog
from raidar.jev.label_export import (
    LabelExportError,
    export_labels,
    load_review_labels,
    write_rows,
)
from raidar.jev.policies import PolicyRegistry
from raidar.jev.report import rubric_shadow_report
from raidar.jev.rubric import RUBRIC_POLICY, CheckpointSource, RubricRunner
from raidar.jev.rubric_questions import load_rubric_question_set
from raidar.jev.session import open_runtime

_NUMBER = re.compile(r"(\d+)")


def register(main: click.Group) -> None:
    jev_calibration.register(jev)
    main.add_command(jev)


def _natural(name: str) -> list[object]:
    return [int(part) if part.isdigit() else part for part in _NUMBER.split(name)]


def checkpoint_sources(
    snapshots: Path, specs: Path | None, within: str = ""
) -> list[CheckpointSource]:
    """Checkpoint snapshots in natural order, each with its cumulative spec.

    ``within`` is the snapshot's path inside each checkpoint directory, for
    trial layouts such as ``steps/<checkpoint>/artifacts/app``.
    """
    names = sorted((path.name for path in snapshots.iterdir() if path.is_dir()), key=_natural)
    sources, parts = [], []
    for name in names:
        spec = None
        instruction = specs / name / "instruction.md" if specs else None
        if instruction is not None and instruction.is_file():
            parts.append(f"## {name}\n\n{instruction.read_text(encoding='utf-8')}")
            spec = "\n\n".join(parts)
        sources.append(CheckpointSource(name=name, root=snapshots / name / within, spec=spec))
    return sources


@click.group()
def jev() -> None:
    """Jev classifier: policies, shadow runs and calibration labels."""


@jev.command("policies")
def jev_policies() -> None:
    """List the versioned Jev policies and their modes."""
    registry = PolicyRegistry.from_dir()
    rows = [registry.get(policy_id) for policy_id in registry.ids()]
    click.echo(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))


@jev.command("shadow-rubric")
@click.option(
    "--snapshots", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True
)
@click.option("--store", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option("--specs", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--replay-only", is_flag=True, help="Never call the provider; replay cached decisions."
)
@click.option(
    "--within",
    default="",
    help="Snapshot path inside each checkpoint directory (e.g. artifacts/app).",
)
@click.option("--trial", help="Trial id for the audit (default: the snapshots directory name).")
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
def jev_shadow_rubric(
    snapshots: Path,
    store: Path,
    specs: Path | None,
    replay_only: bool,
    within: str,
    trial: str | None,
    output: Path | None,
) -> None:
    """Run the SCB rubric question set in shadow over checkpoint snapshots."""
    runtime = open_runtime(store, replay_only=replay_only, trial=trial or snapshots.name)
    runner = RubricRunner(runtime, load_rubric_question_set())
    results = runner.run(checkpoint_sources(snapshots, specs, within))
    report = json.dumps(rubric_shadow_report(results, runtime, policy_id=RUBRIC_POLICY), indent=2)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report + "\n", encoding="utf-8")
    click.echo(report)


@jev.command("export-labels")
@click.option(
    "--labels",
    "labels_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Review-surface label file (review-surface/data/jev-labels.json).",
)
@click.option("--policy", required=True)
@click.option(
    "--store",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Assessment store whose audit the labels must match; skips already-labelled decisions.",
)
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), required=True)
def jev_export_labels(labels_path: Path, policy: str, store: Path | None, output: Path) -> None:
    """Export review-surface labels as JSON lines for `raidar jev import-labels`."""
    try:
        audit = AuditLog.for_store(store) if store is not None else None
        exported = export_labels(load_review_labels(labels_path), policy, audit=audit)
    except LabelExportError as error:
        raise click.ClickException(str(error)) from error
    write_rows(exported, output)
    click.echo(json.dumps({**exported.summary(), "output": str(output)}, indent=2))
