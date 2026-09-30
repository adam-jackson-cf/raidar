"""``raidar analyze``: paired arm comparisons over executed runs (plan WS-J).

Reads one or more run directories of a locked experiment and the Assessment
store, and writes ``analysis.json`` (the machine-readable report the review
surface projects) and ``analysis.md`` (the summary for the gate packet) to
``--output``. Runs and the store are only read; the output directory must lie
outside both.

The verdict thresholds are command-line options until the experiment spec
carries them; the report records them as not pinned by the lock and lists
every one that differs from its default. Runs executed with ``max_checkpoints``
or ``agent_override`` are refused unless ``--allow-dev-runs`` labels the
report as a development run.
"""

from __future__ import annotations

from pathlib import Path

import click

from raidar.analysis.effects import (
    DEFAULT_ALPHA,
    DEFAULT_MAX_EXCLUSION_FRACTION,
    DEFAULT_MIN_PAIRS,
    Settings,
)
from raidar.analysis.load import LoadRequest, load_inputs
from raidar.analysis.markdown import render_markdown
from raidar.analysis.outcomes import outcome_metric
from raidar.analysis.report import analyse
from raidar.analysis.sources import AnalysisInputError
from raidar.scorers.catalog import default_scorer_registry
from raidar.spec.canonical import pretty_canonical_json_bytes
from raidar.spec.references import RegistryReferenceError

REPORT_JSON = "analysis.json"
REPORT_MARKDOWN = "analysis.md"
SETTINGS_SOURCE = "command line"


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
    "runs", nargs=-1, required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "--store",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Assessment store the runs were rescored into.",
)
@click.option(
    "--output",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory for analysis.json and analysis.md (outside the runs and the store).",
)
@click.option(
    "--lock",
    "lock_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Experiment lock (default: the lock next to run.json's experiment_file).",
)
@click.option(
    "--assessment-config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Rescore config used for the Assessments (default: each run's assessment-config.yaml).",
)
@click.option(
    "--min-pairs",
    type=click.IntRange(min=2),
    default=DEFAULT_MIN_PAIRS,
    show_default=True,
    help="Minimum pairs; fewer gives 'insufficient'. Fix it before the data is seen.",
)
@click.option(
    "--max-exclusion-fraction",
    type=click.FloatRange(0.0, 1.0),
    default=DEFAULT_MAX_EXCLUSION_FRACTION,
    show_default=True,
    help="Excluded share of planned pairs above which a verdict is 'insufficient'. "
    "Fix it before the data is seen.",
)
@click.option(
    "--alpha",
    type=click.FloatRange(0.0, 1.0, min_open=True, max_open=True),
    default=DEFAULT_ALPHA,
    show_default=True,
    help="Significance level for the exact McNemar test of final-checkpoint solves.",
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
    help="Smallest effect of interest for the sample-size projection (repeatable).",
)
@click.option(
    "--allow-dev-runs",
    is_flag=True,
    help="Analyse runs executed with max_checkpoints or agent_override, labelled as a "
    "development run (not arm evidence).",
)
def analyze(
    runs: tuple[Path, ...],
    store: Path,
    output: Path,
    lock_path: Path | None,
    assessment_config: Path | None,
    min_pairs: int,
    max_exclusion_fraction: float,
    alpha: float,
    bootstrap_samples: int,
    min_effects: tuple[str, ...],
    allow_dev_runs: bool,
) -> None:
    """Paired deltas, CIs, curves and verdicts for every arm against the baseline."""
    _check_output(output, [*runs, store])
    effects = _min_effects(min_effects)
    request = LoadRequest(
        run_dirs=runs,
        store_root=store,
        registry=default_scorer_registry(),
        lock_path=lock_path,
        assessment_config=assessment_config,
        allow_dev_runs=allow_dev_runs,
    )
    try:
        inputs, lock = load_inputs(request)
        settings = Settings(
            min_pairs=min_pairs,
            max_exclusion_fraction=max_exclusion_fraction,
            alpha=alpha,
            bootstrap_samples=bootstrap_samples,
            seed=int(lock["spec"]["analysis"]["seed"]),
            min_effects=effects,
            source=SETTINGS_SOURCE,
        )
        report = analyse(inputs, settings)
    except (AnalysisInputError, RegistryReferenceError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    output.mkdir(parents=True, exist_ok=True)
    (output / REPORT_JSON).write_bytes(pretty_canonical_json_bytes(report.model_dump(mode="json")))
    (output / REPORT_MARKDOWN).write_text(render_markdown(report))
    if report.dev_run is not None:
        click.echo(f"warning: development run, not arm evidence: {report.dev_run.note}", err=True)
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
