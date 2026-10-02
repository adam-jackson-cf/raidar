"""``raidar analyze``: paired arm comparisons over executed jobs (plan WS-J).

Reads one or more job directories of a locked experiment and the Assessment
store, and writes ``analysis.json`` (the machine-readable report the review
surface projects) and ``analysis.md`` (the summary for the gate packet) to
``--output``. Jobs and the store are only read; the output directory must lie
outside both.

The verdict thresholds (``min_pairs``, ``max_exclusion_fraction``, ``alpha``,
``min_effects``) come from the lock's ``analysis`` section. A command-line option
overrides the pinned value and the report labels it as an override; a lock
that predates pinned thresholds falls back to the documented defaults and the
report says it is not pinned. The report also lists every value that differs
from its default. Jobs executed with ``max_checkpoints``
or ``agent_override`` are refused unless ``--allow-dev-jobs`` labels the
report as a development job.
"""

from __future__ import annotations

from pathlib import Path

import click

from raidar.analysis.load import LoadRequest, load_inputs
from raidar.analysis.markdown import render_markdown
from raidar.analysis.outcomes import outcome_metric
from raidar.analysis.report import analyse
from raidar.analysis.sources import AnalysisInputError
from raidar.analysis.thresholds import settings_from_lock
from raidar.scorers.catalog import default_scorer_registry
from raidar.spec.canonical import pretty_canonical_json_bytes
from raidar.spec.references import RegistryReferenceError

REPORT_JSON = "analysis.json"
REPORT_MARKDOWN = "analysis.md"


def register(main: click.Group) -> None:
    main.add_command(analyze)


def _min_effects(assignments: tuple[str, ...]) -> dict[str, float]:
    effects: dict[str, float] = {}
    for assignment in assignments:
        name, separator, raw = assignment.partition("=")
        if not separator:
            raise click.BadParameter(f"expected METRIC=DELTA, got {assignment!r}")
        try:
            outcome_metric(name)
            effects[name] = float(raw)
        except ValueError as exc:
            raise click.BadParameter(str(exc)) from exc
    return effects


def _check_output(output: Path, protected: list[Path]) -> None:
    resolved = output.resolve()
    for path in protected:
        if resolved.is_relative_to(path.resolve()):
            raise click.UsageError(f"--output must lie outside {path} (analysis never writes runs)")


@click.command()
@click.argument(
    "jobs", nargs=-1, required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "--store",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Assessment store the jobs were rescored into.",
)
@click.option(
    "--output",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory for analysis.json and analysis.md (outside the jobs and the store).",
)
@click.option(
    "--lock",
    "lock_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Experiment lock (default: the lock next to job.json's experiment_file).",
)
@click.option(
    "--assessment-config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Rescore config used for the Assessments (default: the lock's assessment settings).",
)
@click.option(
    "--bundle-search",
    multiple=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Directory to find moved verifier bundles in, by content digest (repeatable).",
)
@click.option(
    "--min-pairs",
    type=click.IntRange(min=2),
    help="Override the lock's minimum pairs (fewer gives 'insufficient'); labelled in "
    "the report as a post-hoc override.",
)
@click.option(
    "--max-exclusion-fraction",
    type=click.FloatRange(0.0, 1.0),
    help="Override the lock's excluded share of planned pairs above which a verdict is "
    "'insufficient'; labelled as an override.",
)
@click.option(
    "--alpha",
    type=click.FloatRange(0.0, 1.0, min_open=True, max_open=True),
    help="Override the lock's significance level for the exact McNemar test of "
    "final-checkpoint solves; labelled as an override.",
)
@click.option(
    "--bootstrap-samples",
    type=click.IntRange(min=100),
    default=10_000,
    show_default=True,
    help="Resamples for bootstrap CIs (used from 10 pairs on; seeded by analysis.seed).",
)
@click.option(
    "--min-effect",
    "min_effects",
    multiple=True,
    metavar="METRIC=DELTA",
    help="Override the lock's smallest effect of interest for one metric in the "
    "sample-size projection (repeatable); labelled as an override.",
)
@click.option(
    "--allow-dev-jobs",
    is_flag=True,
    help="Analyse jobs executed with max_checkpoints or agent_override, labelled as a "
    "development job (not arm evidence).",
)
def analyze(
    jobs: tuple[Path, ...],
    store: Path,
    output: Path,
    lock_path: Path | None,
    assessment_config: Path | None,
    bundle_search: tuple[Path, ...],
    min_pairs: int | None,
    max_exclusion_fraction: float | None,
    alpha: float | None,
    bootstrap_samples: int,
    min_effects: tuple[str, ...],
    allow_dev_jobs: bool,
) -> None:
    """Paired deltas, CIs, curves and verdicts for every arm against the baseline."""
    _check_output(output, [*jobs, store])
    effects = _min_effects(min_effects)
    request = LoadRequest(
        job_dirs=jobs,
        store_root=store,
        registry=default_scorer_registry(),
        lock_path=lock_path,
        assessment_config=assessment_config,
        allow_dev_jobs=allow_dev_jobs,
        bundle_search=bundle_search,
    )
    try:
        inputs, lock = load_inputs(request)
        settings = settings_from_lock(
            lock["spec"]["analysis"],
            overrides={
                "min_pairs": min_pairs,
                "max_exclusion_fraction": max_exclusion_fraction,
                "alpha": alpha,
                "min_effects": effects,
            },
            bootstrap_samples=bootstrap_samples,
        )
        report = analyse(inputs, settings)
    except (AnalysisInputError, RegistryReferenceError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    output.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json")
    if payload.get("errata") is None:
        # A lock without problem errata writes the report exactly as before errata existed.
        payload.pop("errata", None)
    (output / REPORT_JSON).write_bytes(pretty_canonical_json_bytes(payload))
    (output / REPORT_MARKDOWN).write_text(render_markdown(report))
    if report.dev_job is not None:
        click.echo(f"warning: development job, not arm evidence: {report.dev_job.note}", err=True)
    if not report.settings.pinned_by_lock:
        click.echo("warning: the lock does not pin verdict thresholds; defaults used", err=True)
    if report.settings.overridden:
        overridden = ", ".join(report.settings.overridden)
        click.echo(f"warning: lock-pinned thresholds overridden: {overridden}", err=True)
    if report.settings.changed_from_defaults:
        changed = ", ".join(report.settings.changed_from_defaults)
        click.echo(f"warning: verdict settings changed from defaults: {changed}", err=True)
    for comparison in report.comparisons:
        primary = comparison.primary
        click.echo(
            f"{comparison.treatment} vs {comparison.baseline}: primary {primary.metric} "
            f"{primary.verdict.value} ({primary.verdict_reason})"
        )
    click.echo(f"wrote {output / REPORT_JSON} and {output / REPORT_MARKDOWN}")
