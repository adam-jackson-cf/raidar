"""``raidar problem import|validate``: pinned problem import and oracle validation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from raidar.problems.importer import import_problem
from raidar.problems.models import ProblemId
from raidar.problems.source import SourceError
from raidar.problems.store import ProblemStore, ProblemStoreError, find_store_root
from raidar.problems.validation import CheckStatus, Verdict
from raidar.problems.validator import (
    ENGINES,
    ValidateOptions,
    default_report_path,
    validate_problem,
    write_report,
)
from raidar.spec.references import RegistryReferenceError

EXIT_EXCLUDED = 1
EXIT_INFRA = 2
_CHECK_MARK = {
    CheckStatus.PASS: "PASS",
    CheckStatus.FAIL: "FAIL",
    CheckStatus.INFRA: "INFRA",
    CheckStatus.NOT_RUN: "SKIP",
}

_STORE_OPTION = click.option(
    "--store",
    "store_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Problems store root (default: the nearest problems/ with sources.yaml).",
)
_CHECKOUT_OPTION = click.option(
    "--source-checkout",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Existing dataset checkout at the pinned commit (default: clone into the store cache).",
)


def register(main: click.Group) -> None:
    main.add_command(problem)


def _store(store_root: Path | None) -> ProblemStore:
    try:
        return ProblemStore(store_root.resolve() if store_root else find_store_root(Path.cwd()))
    except ProblemStoreError as exc:
        raise click.ClickException(str(exc)) from exc


def _problem_id(ref: str) -> ProblemId:
    try:
        return ProblemId.parse(ref)
    except RegistryReferenceError as exc:
        raise click.BadParameter(str(exc), param_hint="REF") from exc


@click.group()
def problem() -> None:
    """Pinned problem import and oracle validation."""


@problem.command("import")
@click.argument("refs", nargs=-1, required=True)
@_STORE_OPTION
@_CHECKOUT_OPTION
@click.option("--update", is_flag=True, help="Accept content that differs from the manifest.")
def import_command(
    refs: tuple[str, ...], store_root: Path | None, source_checkout: Path | None, update: bool
) -> None:
    """Import REFS (<dataset>/<problem>[.<checkpoint>]@<version>) from the pinned dataset."""
    store = _store(store_root)
    for ref in refs:
        try:
            result = import_problem(
                store, _problem_id(ref), source_checkout=source_checkout, update=update
            )
        except (ProblemStoreError, SourceError, ValueError) as exc:
            raise click.ClickException(f"{ref}: {exc}") from exc
        manifest = result.manifest
        state = "imported" if result.changed else "verified (matches manifest)"
        click.echo(f"{manifest.ref}: {state}")
        click.echo(f"  mode: {manifest.mode.value}  checkpoints: {manifest.checkpoints}")
        click.echo(f"  source: {manifest.source.url}@{manifest.source.commit}")
        click.echo(f"  manifest: {result.manifest_path}  sha256: {manifest.digest()}")
        click.echo(f"  content: {result.content_dir}")


@problem.command("validate")
@click.argument("ref")
@_STORE_OPTION
@_CHECKOUT_OPTION
@click.option("--engine", type=click.Choice(ENGINES), default="direct", show_default=True)
@click.option("--repeats", type=click.IntRange(min=1), default=3, show_default=True)
@click.option("--concurrency", type=click.IntRange(min=1), default=4, show_default=True)
@click.option("--docker-context", default="orbstack", show_default=True)
@click.option(
    "--native-runner",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="SCB fork checkout for the native parity arm (skipped when omitted).",
)
@click.option(
    "--report",
    "report_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Report path (default: the manifest's <version>.validation.json; an infra "
        "failure goes to the store cache instead, never over the committed report)."
    ),
)
def validate_command(
    ref: str,
    store_root: Path | None,
    source_checkout: Path | None,
    engine: str,
    repeats: int,
    concurrency: int,
    docker_context: str,
    native_runner: Path | None,
    report_path: Path | None,
) -> None:
    """Validate REF's oracle: ref k passes 1..k, ref k-1 fails k, repeats agree, native parity.

    Exit 0 when validated (fully or with skipped checks), 1 when a check
    failed (exclude the problem), 2 when infrastructure failed (no verdict).
    """
    store = _store(store_root)
    problem_id = _problem_id(ref)
    options = ValidateOptions(
        engine=engine,
        repeats=repeats,
        concurrency=concurrency,
        docker_context=docker_context,
        native_runner=native_runner,
        source_checkout=source_checkout,
    )
    try:
        report = asyncio.run(validate_problem(store, problem_id, options))
    except (ProblemStoreError, SourceError, RuntimeError, ValueError) as exc:
        click.echo(f"INFRA: {exc}", err=True)
        raise SystemExit(EXIT_INFRA) from exc
    path = write_report(report, report_path or default_report_path(store, report))
    for check in report.checks:
        click.echo(f"[{_CHECK_MARK[check.status]}] {check.name}")
        for line in check.detail:
            click.echo(f"    {line}")
    payload = report.to_json()
    click.echo(f"headline policy: {json.dumps(payload['headline_policy'])}")
    not_run = payload["not_run"]
    skipped = f" (not run: {', '.join(not_run)})" if isinstance(not_run, list) and not_run else ""
    click.echo(f"{report.problem}: {report.verdict.value.upper()}{skipped}")
    click.echo(f"report: {path}")
    if report.verdict is Verdict.EXCLUDED:
        raise SystemExit(EXIT_EXCLUDED)
    if report.verdict is Verdict.INFRA_FAILURE:
        raise SystemExit(EXIT_INFRA)
