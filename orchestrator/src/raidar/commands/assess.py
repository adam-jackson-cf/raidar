"""`raidar assess`: offline assessment over stored runs.

``--regrade`` reruns the oracle over a trial's (or job's) stored per-step
snapshots and reports whether the results match the original run (plan E3).

``--rescore`` runs scorers over a trial's (or job's) stored snapshots, oracle
records and traces, and adds content-addressed Assessments to ``--store``
(plan F2). It never writes to the run. ``--replay`` reruns scorers whose
Assessment already exists and fails unless the bytes are identical.
``--config`` supplies the assessor inventory policy and per-scorer config.
Without it, the run's own ``assessment-config.yaml`` (the locked assessor
settings the run was executed with, found at the target or up to two levels
above it: ``<run>/trials/<trial>``) is used for the scorers being run, so a
plain rescore stores Assessments under the keys analysis looks up. The report
names the config it used.
Scorers that decide under Jev (``process-compliance@1``) get a runtime whose
audit and cache live in ``--store``; ``--jev-replay-only`` answers from that
cache alone and never calls the provider.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import click

from raidar.execution.assessment import ASSESSMENT_CONFIG_FILE
from raidar.oracle.bundle import BundleError
from raidar.oracle.regrade import RegradeError, RegradeOptions, regrade_path
from raidar.scorers.catalog import ScorerUnavailableError, default_scorer_registry
from raidar.scorers.rescore import report_payload, rescore_path
from raidar.scorers.rescore_config import RescoreConfig, load_rescore_config
from raidar.scorers.store import AssessmentConflictError, AssessmentStore


def register(main: click.Group) -> None:
    main.add_command(assess)


def _emit(report: dict[str, object], out: Path | None) -> None:
    text = json.dumps(report, indent=2)
    if out is not None:
        out.write_text(text + "\n")
    click.echo(text)


def _regrade(target: Path, options: RegradeOptions, out: Path | None) -> None:
    try:
        report = asyncio.run(regrade_path(target, options))
    except (RegradeError, BundleError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(report, out)
    if not report["match"]:
        raise click.exceptions.Exit(1)


@dataclass(frozen=True)
class _RescoreRequest:
    target: Path
    scorer_refs: tuple[str, ...]
    store: Path | None
    config: Path | None
    bundle: Path | None
    replay: bool
    jev_replay_only: bool = False


# A trial dir sits at <run>/trials/<trial>; a job dir at <run>/trials.
_RUN_CONFIG_DEPTH = 2


def run_assessment_config(target: Path) -> Path | None:
    """The ``assessment-config.yaml`` of the run that holds ``target``, if any."""
    candidate = target.resolve()
    for _ in range(_RUN_CONFIG_DEPTH + 1):
        if (candidate / ASSESSMENT_CONFIG_FILE).is_file():
            return candidate / ASSESSMENT_CONFIG_FILE
        candidate = candidate.parent
    return None


def _settings(request: _RescoreRequest) -> tuple[RescoreConfig, Path | None]:
    """Explicit ``--config`` as given; else the run's config narrowed to the scorers run."""
    if request.config is not None:
        return load_rescore_config(request.config), request.config
    found = run_assessment_config(request.target)
    if found is None:
        return RescoreConfig(), None
    locked = load_rescore_config(found)
    scorers = {ref: config for ref, config in locked.scorers.items() if ref in request.scorer_refs}
    return locked.model_copy(update={"scorers": scorers}), found


def _rescore(request: _RescoreRequest, out: Path | None) -> None:
    store = request.store
    if store is None:
        raise click.UsageError("--rescore needs --store DIR (kept outside the run directory)")
    if store.resolve().is_relative_to(request.target.resolve()):
        raise click.UsageError("--store must not be inside the run being rescored")
    try:
        settings, config_path = _settings(request)
        records = rescore_path(
            request.target,
            request.scorer_refs,
            AssessmentStore(store),
            default_scorer_registry(),
            config=settings.scorers,
            inventory_policy=settings.inventory_policy,
            bundle_override=request.bundle,
            replay=request.replay,
            jev_replay_only=request.jev_replay_only,
        )
    except (ValueError, ScorerUnavailableError, AssessmentConflictError) as exc:
        # ValueError covers unknown references, invalid run inputs and rejected scorer output.
        raise click.ClickException(str(exc)) from exc
    _emit({"config": str(config_path) if config_path else None, **report_payload(records)}, out)


@click.command()
@click.option(
    "--regrade",
    "regrade_target",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Trial or job directory whose stored snapshots the oracle regrades.",
)
@click.option(
    "--rescore",
    "rescore_target",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Trial or job directory to assess with --scorer into --store.",
)
@click.option(
    "--scorer",
    "scorer_refs",
    multiple=True,
    help="Scorer reference (name@version) for --rescore; repeatable.",
)
@click.option(
    "--store",
    type=click.Path(file_okay=False, path_type=Path),
    help="Assessment store directory for --rescore (outside the run).",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help=(
        "With --rescore, YAML with inventory_policy and per-scorer config (scorers: {ref: {}}); "
        "default: the run's assessment-config.yaml."
    ),
)
@click.option(
    "--replay",
    is_flag=True,
    help="With --rescore, rerun existing Assessments and require byte-identical output.",
)
@click.option(
    "--jev-replay-only",
    is_flag=True,
    help="With --rescore, answer Jev decisions from the store's cache only (never the provider).",
)
@click.option(
    "--bundle",
    "bundle_override",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Oracle bundle location if it moved (its digest must still match the records).",
)
@click.option("--docker-context", default="orbstack", show_default=True)
@click.option(
    "--out", type=click.Path(dir_okay=False, path_type=Path), help="Write the report here."
)
def assess(
    regrade_target: Path | None,
    rescore_target: Path | None,
    scorer_refs: tuple[str, ...],
    store: Path | None,
    config_path: Path | None,
    replay: bool,
    jev_replay_only: bool,
    bundle_override: Path | None,
    docker_context: str,
    out: Path | None,
) -> None:
    """Re-assess stored runs without re-running agents."""
    if regrade_target is not None and rescore_target is None:
        options = RegradeOptions(docker_context=docker_context, bundle_override=bundle_override)
        _regrade(regrade_target, options, out)
    elif rescore_target is not None and regrade_target is None:
        request = _RescoreRequest(
            rescore_target,
            scorer_refs,
            store,
            config_path,
            bundle_override,
            replay,
            jev_replay_only,
        )
        _rescore(request, out)
    else:
        raise click.UsageError("choose one assessment mode: --regrade PATH or --rescore PATH")
