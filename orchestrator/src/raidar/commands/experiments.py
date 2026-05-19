"""Experiment artifact CLI commands."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import click

from raidar.commands.shared import (
    DEFAULT_ARCHIVE_ROOT,
    EXPERIMENT_KIND_CHOICES,
    REPO_ROOT,
    load_json_file,
    resolve_experiments_root,
)


def register(main: click.Group) -> None:
    main.add_command(experiments)


@click.group()
def experiments() -> None:
    """Experiment artifact workflows."""


def execution_payload(execution_dir: Path) -> dict[str, object]:
    for candidate in (
        execution_dir / "experiment-summary.json",
        execution_dir / "experiment.json",
        execution_dir / "runs" / "run-01" / "run.json",
    ):
        payload = load_json_file(candidate)
        if payload is not None:
            return payload
    return {}


def execution_name_parts(execution_id: str) -> tuple[str | None, str | None, str | None]:
    parts = execution_id.split("__")
    if len(parts) < 3:
        return None, None, None
    return parts[0], parts[1], parts[2]


def execution_record(execution_dir: Path) -> dict[str, object]:
    payload = execution_payload(execution_dir)
    config = payload.get("config")
    aggregate = payload.get("aggregate")
    config_dict = config if isinstance(config, dict) else {}
    aggregate_dict = aggregate if isinstance(aggregate, dict) else {}
    _, scenario_from_name, revision_from_name = execution_name_parts(execution_dir.name)
    scenario_name = str(
        config_dict.get("scenario_name") or scenario_from_name or "unknown-scenario"
    )
    scenario_revision = str(
        config_dict.get("scenario_revision") or revision_from_name or "unknown-revision"
    )
    return {
        "execution_id": execution_dir.name,
        "path": str(execution_dir),
        "created_at_utc": payload.get("created_at_utc"),
        "scenario_name": scenario_name,
        "scenario_revision": scenario_revision,
        "harness": config_dict.get("harness"),
        "model": config_dict.get("model"),
        "evaluation_profile": config_dict.get("evaluation_profile"),
        "metrics": config_dict.get("metrics"),
        "run_count_total": aggregate_dict.get("run_count_total"),
        "unscored_count": aggregate_dict.get("unscored_count"),
    }


def execution_model_key(execution_dir: Path) -> str:
    payload = execution_payload(execution_dir)
    config = payload.get("config")
    config_dict = config if isinstance(config, dict) else {}
    model = config_dict.get("model")
    if isinstance(model, str) and model:
        return model.replace("/", "__")
    return "unknown-model"


def archive_destination(src: Path, archive_dir: Path) -> Path:
    try:
        rel = src.relative_to(REPO_ROOT)
    except ValueError:
        rel = Path("experiments") / src.name
    return archive_dir / rel


def archive_path(src: Path, archive_dir: Path, *, dry_run: bool) -> bool:
    if not src.exists():
        return False
    destination = archive_destination(src, archive_dir)
    rel = destination.relative_to(archive_dir)
    if dry_run:
        click.echo(f"would-archive: {rel}")
        return True
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(destination))
    click.echo(f"archived: {rel}")
    return True


def sorted_experiment_dirs(experiments_root: Path) -> list[Path]:
    if not experiments_root.is_dir():
        return []
    return sorted(
        (path for path in experiments_root.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )


def default_archive_dir() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return DEFAULT_ARCHIVE_ROOT / "raidar-archive" / stamp


def execution_matches_filters(record: dict[str, object], filters: dict[str, object]) -> bool:
    scenario_value = str(record.get("scenario_name", "")).lower()
    model_value = str(record.get("model", "")).lower()
    harness_value = str(record.get("harness", "")).lower()
    evaluation_profile_value = str(record.get("evaluation_profile", "")).lower()
    scenario = filters.get("scenario")
    model = filters.get("model")
    harness = filters.get("harness")
    evaluation_profile = filters.get("evaluation_profile")
    if isinstance(scenario, str) and scenario.lower() not in scenario_value:
        return False
    if isinstance(model, str) and model.lower() not in model_value:
        return False
    if isinstance(harness, str) and harness.lower() not in harness_value:
        return False
    return not (
        isinstance(evaluation_profile, str)
        and evaluation_profile.lower() not in evaluation_profile_value
    )


@experiments.command("list")
@click.option("--experiments-root", type=click.Path(path_type=Path), default=None)
@click.option(
    "--experiment-kind",
    type=click.Choice(EXPERIMENT_KIND_CHOICES),
    default="benchmark",
    show_default=True,
)
@click.option("--scenario", type=str, help="Filter by scenario name substring.")
@click.option("--model", type=str, help="Filter by model substring.")
@click.option("--harness", type=str, help="Filter by harness substring.")
@click.option("--evaluation-profile", type=str, help="Filter by evaluation profile substring.")
@click.option("--limit", type=click.IntRange(min=1), default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def experiments_list(**options) -> None:
    """List experiments with optional filters."""
    resolved_root = resolve_experiments_root(
        experiments_root=options["experiments_root"],
        experiment_kind=options["experiment_kind"],
    )
    rows: list[dict[str, object]] = []
    for path in sorted_experiment_dirs(resolved_root):
        record = execution_record(path)
        if not execution_matches_filters(record, options):
            continue
        rows.append(record)
        if len(rows) >= options["limit"]:
            break

    if options["as_json"]:
        click.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        click.echo("No experiments found.")
        return
    for index, row in enumerate(rows, start=1):
        click.echo(
            f"{index:02d}. {row['execution_id']} | "
            f"scenario={row['scenario_name']}@{row['scenario_revision']} | "
            f"harness={row.get('harness') or 'unknown'} | "
            f"model={row.get('model') or 'unknown'} | "
            f"evaluation_profile={row.get('evaluation_profile') or 'unknown'} | "
            f"runs={row.get('run_count_total') or 0} | unscored={row.get('unscored_count') or 0}"
        )


@experiments.command("prune")
@click.option("--experiments-root", type=click.Path(path_type=Path), default=None)
@click.option(
    "--experiment-kind",
    type=click.Choice(EXPERIMENT_KIND_CHOICES),
    default="benchmark",
    show_default=True,
)
@click.option("--keep-per-model", type=click.IntRange(min=0), default=1, show_default=True)
@click.option("--archive-dir", type=click.Path(path_type=Path))
@click.option("--dry-run", is_flag=True, help="Show actions without moving files.")
def experiments_prune(**options) -> None:
    """Archive stale experiment artifacts while keeping latest experiments per model."""
    archive_root = (options["archive_dir"] or default_archive_dir()).resolve()
    experiments_root = resolve_experiments_root(
        experiments_root=options["experiments_root"],
        experiment_kind=options["experiment_kind"],
    )
    if not options["dry_run"]:
        archive_root.mkdir(parents=True, exist_ok=True)

    kept_counts: dict[str, int] = {}
    pruned_count = 0
    for execution_dir in sorted_experiment_dirs(experiments_root):
        model_key = execution_model_key(execution_dir)
        count = kept_counts.get(model_key, 0)
        if count < options["keep_per_model"]:
            kept_counts[model_key] = count + 1
            continue
        if archive_path(execution_dir, archive_root, dry_run=options["dry_run"]):
            pruned_count += 1

    click.echo(f"archive_dir={archive_root}")
    click.echo(f"experiments_pruned={pruned_count}")
