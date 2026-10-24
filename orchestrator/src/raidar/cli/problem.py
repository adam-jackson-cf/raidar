"""``raidar problem list|info|import|validate``: the pinned problem registry."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from raidar.cli.target_options import docker_selection, target_option
from raidar.design.experiments.references import RegistryReferenceError
from raidar.design.problems.errata import Disposition, draft_errata, report_entries
from raidar.design.problems.importer import import_problem
from raidar.design.problems.models import ProblemId, ProblemManifest
from raidar.design.problems.source import SourceError
from raidar.design.problems.store import ProblemStore, ProblemStoreError, open_store, store_at
from raidar.design.problems.validation import (
    Check,
    CheckStatus,
    ValidationRecordError,
    Verdict,
    read_validation_record,
)
from raidar.design.problems.validator import (
    ENGINES,
    ValidateOptions,
    audit_problem,
    default_report_path,
    merge_audit,
    validate_problem,
    write_report,
)

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
    help="Problems store root (default: the nearest lab/problems/ with sources.yaml).",
)
_CHECKOUT_OPTION = click.option(
    "--source-checkout",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Existing dataset checkout at the pinned commit (default: clone into var/problems/cache).",
)


def register(main: click.Group) -> None:
    main.add_command(problem)


def _store(store_root: Path | None) -> ProblemStore:
    try:
        return store_at(store_root) if store_root else open_store(Path.cwd())
    except ProblemStoreError as exc:
        raise click.ClickException(str(exc)) from exc


def _problem_id(ref: str) -> ProblemId:
    try:
        return ProblemId.parse(ref)
    except RegistryReferenceError as exc:
        raise click.BadParameter(str(exc), param_hint="REF") from exc


@click.group()
def problem() -> None:
    """Pinned problem registry: list, inspect, import and verifier-validate problems."""


def _validation_state(store: ProblemStore, problem_id: ProblemId, manifest: ProblemManifest) -> str:
    """The recorded verdict, or why there is no usable one for this manifest."""
    try:
        record = read_validation_record(store.validation_path(problem_id))
    except ValidationRecordError:
        return "not validated"
    if record.manifest_sha256 != manifest.digest():
        return f"stale ({record.verdict.value} for another manifest)"
    usable = "usable" if record.usable else "not usable"
    not_run = f"; not run: {', '.join(record.not_run)}" if record.not_run else ""
    return f"{record.verdict.value} ({usable}{not_run})"


def _errata_state(store: ProblemStore, problem_id: ProblemId, manifest: ProblemManifest) -> str:
    try:
        errata = store.load_errata(problem_id, manifest)
    except ProblemStoreError as exc:
        return f"invalid ({exc})"
    if errata is None:
        return "none"
    excluded = errata.errata.ids(Disposition.EXCLUDE_FROM_SOLVED)
    informational = errata.errata.ids(Disposition.INFORMATIONAL)
    return (
        f"{store.errata_path(problem_id)} sha256 {errata.sha256[:12]} "
        f"({len(excluded)} exclude-from-solved, {len(informational)} informational)"
    )


def _store_state(store: ProblemStore, problem_id: ProblemId) -> str:
    content = store.content_dir(problem_id)
    return "imported" if (content / "task").is_dir() else "not imported"


@problem.command("list")
@_STORE_OPTION
def list_command(store_root: Path | None) -> None:
    """List every registered problem with its mode, validation and import state."""
    store = _store(store_root)
    try:
        problems = store.registered()
        rows = [(problem_id, store.load_manifest(problem_id)) for problem_id in problems]
    except ProblemStoreError as exc:
        raise click.ClickException(str(exc)) from exc
    if not rows:
        click.echo(f"No problems registered under {store.root / 'registry'}")
        return
    for problem_id, manifest in rows:
        click.echo(str(problem_id))
        click.echo(f"  mode: {manifest.mode.value}  checkpoints: {manifest.checkpoints}")
        click.echo(f"  validation: {_validation_state(store, problem_id, manifest)}")
        click.echo(f"  errata: {_errata_state(store, problem_id, manifest)}")
        click.echo(f"  store: {_store_state(store, problem_id)}")


@problem.command("info")
@click.argument("ref")
@_STORE_OPTION
def info_command(ref: str, store_root: Path | None) -> None:
    """Show REF's manifest: steps, source pin, pack binding, validation and import state."""
    store = _store(store_root)
    problem_id = _problem_id(ref)
    try:
        manifest = store.load_manifest(problem_id)
    except ProblemStoreError as exc:
        raise click.ClickException(str(exc)) from exc
    source = manifest.source
    click.echo(str(problem_id))
    click.echo(f"  mode: {manifest.mode.value}  checkpoints: {manifest.checkpoints}")
    click.echo(f"  steps: {', '.join(step.name for step in manifest.steps)}")
    click.echo(f"  source: {source.url}@{source.commit} ({source.problem})")
    click.echo(f"  pack: {manifest.pack.id}@{manifest.pack.version} image {manifest.pack.image}")
    if manifest.entry_file is not None:
        click.echo(f"  entry_file: {manifest.entry_file}")
    click.echo(f"  manifest: {store.manifest_path(problem_id)}  sha256: {manifest.digest()}")
    click.echo(f"  validation: {_validation_state(store, problem_id, manifest)}")
    click.echo(f"  validation report: {store.validation_path(problem_id)}")
    click.echo(f"  errata: {_errata_state(store, problem_id, manifest)}")
    click.echo(f"  store: {_store_state(store, problem_id)} ({store.content_dir(problem_id)})")


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
@target_option
@click.option(
    "--native-runner",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="SCB fork checkout for the native parity arm (skipped when omitted).",
)
@click.option(
    "--audit/--no-audit",
    default=True,
    show_default=True,
    help="Run the spec-determinacy audit (static + reference variants) into the report.",
)
@click.option(
    "--audit-only",
    is_flag=True,
    help="Run only the audit and write it into the committed report of the current manifest.",
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
    target_name: str | None,
    native_runner: Path | None,
    audit: bool,
    audit_only: bool,
    report_path: Path | None,
) -> None:
    """Validate REF's verifier: ref k passes 1..k, ref k-1 fails k, repeats agree, native parity.

    The spec-determinacy audit flags verifier cases that assert what the
    specification does not determine; it never changes the verdict.
    Exit 0 when validated (fully or with skipped checks), 1 when a check
    failed (exclude the problem), 2 when infrastructure failed (no verdict).
    """
    store = _store(store_root)
    problem_id = _problem_id(ref)
    selection = docker_selection(
        target_name,
        trial_purpose="problem validation (hidden tests)",
        grader_purpose="problem validation (hidden tests)",
    )
    options = ValidateOptions(
        engine=engine,
        repeats=repeats,
        concurrency=selection.trial.effective_concurrency(concurrency),
        docker_context=selection.grader.docker_context,
        native_runner=native_runner,
        source_checkout=source_checkout,
        audit=audit or audit_only,
    )
    if audit_only:
        return _audit_only(store, problem_id, options)
    try:
        report = asyncio.run(validate_problem(store, problem_id, options))
    except (ProblemStoreError, SourceError, RuntimeError, ValueError) as exc:
        click.echo(f"INFRA: {exc}", err=True)
        raise SystemExit(EXIT_INFRA) from exc
    path = write_report(report, report_path or default_report_path(store, report))
    _echo_checks(report.checks)
    payload = report.to_json()
    click.echo(f"headline metric: {json.dumps(payload['headline_policy'])}")
    not_run = payload["not_run"]
    skipped = f" (not run: {', '.join(not_run)})" if isinstance(not_run, list) and not_run else ""
    _echo_audit(report.spec_determinacy)
    click.echo(f"{report.problem}: {report.verdict.value.upper()}{skipped}")
    click.echo(f"report: {path}")
    if report.verdict is Verdict.EXCLUDED:
        raise SystemExit(EXIT_EXCLUDED)
    if report.verdict is Verdict.INFRA_FAILURE:
        raise SystemExit(EXIT_INFRA)


def _echo_checks(checks: list[Check]) -> None:
    for check in checks:
        click.echo(f"[{_CHECK_MARK[check.status]}] {check.name}")
        for line in check.detail:
            click.echo(f"    {line}")


def _echo_audit(audit: dict[str, object] | None) -> None:
    if audit is None:
        return
    summary = audit.get("summary")
    dynamic = audit.get("dynamic")
    status = dynamic.get("status") if isinstance(dynamic, dict) else None
    click.echo(f"spec-determinacy audit ({status}): {json.dumps(summary, sort_keys=True)}")
    for entry in report_entries(audit.get("flagged")):
        rules = sorted({str(a["rule"]) for a in report_entries(entry.get("assertions"))})
        click.echo(f"  [FLAG] {entry['case']} ({', '.join(rules)})")


def _audit_only(store: ProblemStore, problem_id: ProblemId, options: ValidateOptions) -> None:
    try:
        audit = asyncio.run(audit_problem(store, problem_id, options))
        path = merge_audit(store, problem_id, audit)
    except (ProblemStoreError, ValidationRecordError, RuntimeError, ValueError) as exc:
        click.echo(f"INFRA: {exc}", err=True)
        raise SystemExit(EXIT_INFRA) from exc
    _echo_audit(audit)
    click.echo(f"report: {path} (spec_determinacy updated; checks and verdict unchanged)")


@problem.command("errata-draft")
@click.argument("ref")
@_STORE_OPTION
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="Where to write the draft (review it, then commit it as <version>.errata.yaml).",
)
@click.option("--registered", required=True, help="ISO date the errata is registered.")
def errata_draft_command(
    ref: str, store_root: Path | None, output_path: Path, registered: str
) -> None:
    """Draft REF's errata from its validation report's spec-determinacy audit."""
    store = _store(store_root)
    problem_id = _problem_id(ref)
    try:
        manifest = store.load_manifest(problem_id)
        report = json.loads(store.validation_path(problem_id).read_text())
    except (ProblemStoreError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    audit = report.get("spec_determinacy")
    if report.get("manifest_sha256") != manifest.digest() or not isinstance(audit, dict):
        raise click.ClickException(
            f"{store.validation_path(problem_id)} has no spec_determinacy audit for the current "
            "manifest; run `raidar problem validate --audit-only` first"
        )
    output_path.write_text(
        draft_errata(str(problem_id), manifest.digest(), audit, registered=registered)
    )
    click.echo(f"wrote {output_path}: {len(audit.get('flagged') or [])} flagged case(s)")
