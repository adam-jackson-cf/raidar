"""`raidar assess`: offline assessment over stored jobs.

``--regrade`` reruns the verifier over a trial's (or job's) stored per-step
snapshots and reports whether the results match the original run (plan E3).

``--rescore`` runs scorers over a trial's (or job's) stored snapshots, oracle
records and traces, and adds content-addressed Assessments to ``--store``
(plan F2). It never writes to the job. ``--replay`` reruns scorers whose
Assessment already exists and fails unless the bytes are identical.
``--config`` supplies the assessor inventory policy and per-scorer config.
Without it, the settings come from the experiment lock of the job that holds
the target (found at the target or up to two levels above it:
``<job>/trials/<trial>``): ``--lock`` if given, else the experiment's current
lock when its ``lock_sha256`` is the job's, else the job's ``job.lock.json``.
A relock that changes only assessment settings keeps ``lock_sha256``, so a
plain rescore stores Assessments under the keys analysis looks up for that
lock. Only a job with no findable lock falls back to its execution-time
``assessment-config.yaml``. The report names the config it used.
Scorers that decide under Jev (only the dormant ``process-compliance@1``; Jev
is parked and its policy is off) get a runtime whose audit and cache live in
``--store``; ``--jev-replay-only`` answers from that cache alone and never
calls the provider. ``process-compliance@2`` binds no Jev runtime.

Pre-fix grades: when a scorer run reads them (``gate-effort@4`` to ``@6``), ``--rescore``
first grades every guardrail-gate@3 pre-fix snapshot of the target that is not
yet graded into ``--store`` (``raidar.verifier.prefix_grade``: the step's own
bundle, sandbox and timeout, on ``--docker-context``), never into the job, and
reports what it graded. ``--replay`` grades nothing. ``--regrade`` with
``--store`` also regrades the stored pre-fix grades beside the final snapshots.

Offline coverage: when a scorer run reads it (``code-quality@2``), ``--rescore``
first makes, into ``--store``, the verifier's coverage run of every step whose
trial deferred it (``protocol.verifier_coverage: offline``;
``raidar.verifier.coverage_offline``), never into the job, and reports what it
made. ``--replay`` makes nothing. ``--regrade --coverage-check`` instead makes
every step's coverage run offline and compares it with the trial's own coverage
pass (parity verdict and G input) or, for a deferred step, the run stored in
``--store``; it writes nothing and fails on any difference.

``AS1`` (the assess-time half of the runtime-compatibility gate): CLI-supplied
``--scorer`` values must cover the runtime of the job's lock, and a ``--bundle``
(rescore or regrade) must be the verifier bundle of a problem that lock binds.
A target outside any job has no recorded runtime, so only runtime-agnostic
scorers are accepted there unless ``--lock`` names the lock. Legacy jobs are
refused until ``raidar migrate-job`` moves them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from raidar.analysis.sources import AnalysisInputError, JobDir, default_lock, read_lock
from raidar.commands.assess_grading import (
    RegradeOptions,
    check_coverage,
    grade_pre_fix,
    produce_coverage,
    regrade,
)
from raidar.execution.assessment import ASSESSMENT_CONFIG_FILE, lock_rescore_config
from raidar.job_layout import JOB_MANIFEST, LegacyJobError, is_legacy_job, legacy_job_message
from raidar.scorers.catalog import ScorerUnavailableError, default_scorer_registry
from raidar.scorers.rescore import report_payload, rescore_path
from raidar.scorers.rescore_config import RescoreConfig, load_rescore_config
from raidar.scorers.store import AssessmentConflictError, AssessmentStore
from raidar.verifier.bundle import BundleError, load_bundle


def register(main: click.Group) -> None:
    main.add_command(assess)


def _emit(report: dict[str, object], out: Path | None) -> None:
    text = json.dumps(report, indent=2)
    if out is not None:
        out.write_text(text + "\n")
    click.echo(text)


def _regrade(
    target: Path,
    options: RegradeOptions,
    out: Path | None,
    store: Path | None = None,
    *,
    coverage_check: bool = False,
) -> None:
    report = (
        check_coverage(target, options, store)
        if coverage_check
        else regrade(target, options, store)
    )
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
    lock: Path | None = None
    docker_context: str = "orbstack"


# A trial dir sits at <job>/trials/<trial>; a job dir at <job>/trials.
_JOB_CONFIG_DEPTH = 2


def run_assessment_config(target: Path) -> Path | None:
    """The ``assessment-config.yaml`` of the job that holds ``target``, if any."""
    candidate = target.resolve()
    for _ in range(_JOB_CONFIG_DEPTH + 1):
        if (candidate / ASSESSMENT_CONFIG_FILE).is_file():
            return candidate / ASSESSMENT_CONFIG_FILE
        candidate = candidate.parent
    return None


def _job_dir(target: Path) -> Path | None:
    candidate = target.resolve()
    for _ in range(_JOB_CONFIG_DEPTH + 1):
        if is_legacy_job(candidate):
            raise LegacyJobError(legacy_job_message(candidate))
        if (candidate / JOB_MANIFEST).is_file():
            return candidate
        candidate = candidate.parent
    return None


ANY_RUNTIME = "*"


def _scorer_issues(runtime: str | None, scorer_refs: tuple[str, ...]) -> list[str]:
    registry = default_scorer_registry()
    where = (
        f"the job's runtime is {runtime}"
        if runtime
        else "the target is outside any job, so its runtime is unknown (pass --lock)"
    )
    issues = []
    for ref in scorer_refs:
        try:
            runtimes = registry.get(ref).manifest.runtimes
        except (KeyError, ValueError):
            continue  # unknown scorers are reported by the rescore itself
        if ANY_RUNTIME not in runtimes and runtime not in runtimes:
            issues.append(f"[AS1] scorer {ref} supports runtimes [{', '.join(runtimes)}]; {where}")
    return issues


def _bundle_issues(lock: dict[str, Any], bundle: Path) -> list[str]:
    problems = lock["execution"]["references"].get("problem", {})
    bound = {str(record["metadata"].get("bundle_sha256")): ref for ref, record in problems.items()}
    try:
        digest = load_bundle(bundle).digest()
    except BundleError as exc:
        return [f"[AS1] --bundle {bundle} is not a verifier bundle: {exc}"]
    if digest in bound:
        return []
    return [
        f"[AS1] --bundle {bundle} (digest {digest[:12]}) is not the verifier bundle "
        f"of any problem the job's lock binds ({', '.join(sorted(bound.values()))})"
    ]


def as1_issues(
    lock: dict[str, Any] | None, scorer_refs: tuple[str, ...], bundle: Path | None
) -> list[str]:
    """``AS1``: scorers and a bundle override against the job lock's runtime and bindings."""
    runtime = str(lock["spec"]["runtime"]) if lock is not None else None
    issues = _scorer_issues(runtime, scorer_refs)
    if bundle is not None and lock is not None:
        issues.extend(_bundle_issues(lock, bundle))
    return issues


def _job_lock(target: Path, explicit: Path | None) -> dict[str, Any] | None:
    try:
        path = run_lock(target, explicit)
    except (AnalysisInputError, LegacyJobError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    return read_lock(path) if path is not None else None


def _check_as1(
    target: Path, explicit: Path | None, refs: tuple[str, ...], bundle: Path | None
) -> None:
    issues = as1_issues(_job_lock(target, explicit), refs, bundle)
    if issues:
        raise click.ClickException("assess refused:\n  " + "\n  ".join(issues))


def run_lock(target: Path, explicit: Path | None) -> Path | None:
    """The lock whose assessment settings a rescore of ``target`` uses, if one is found."""
    job_dir = _job_dir(target)
    if job_dir is None:
        return explicit
    manifest = json.loads((job_dir / JOB_MANIFEST).read_text())
    path = explicit or default_lock(JobDir(path=job_dir, manifest=manifest, plan={}, records=()))
    if not path.is_file():
        return None
    lock = read_lock(path)
    if lock["lock_sha256"] != manifest.get("lock_sha256"):
        raise ValueError(
            f"lock {path} ({str(lock['lock_sha256'])[:12]}) is not the lock job {job_dir} "
            f"executed under ({str(manifest.get('lock_sha256'))[:12]})"
        )
    return path


def _narrowed(config: RescoreConfig, refs: tuple[str, ...]) -> RescoreConfig:
    scorers = {ref: settings for ref, settings in config.scorers.items() if ref in refs}
    return config.model_copy(update={"scorers": scorers})


def _settings(request: _RescoreRequest) -> tuple[RescoreConfig, Path | None]:
    """``--config`` as given; else the job's lock, narrowed to the scorers run; else its file."""
    if request.config is not None:
        return load_rescore_config(request.config), request.config
    try:
        lock_path = run_lock(request.target, request.lock)
    except AnalysisInputError as exc:
        raise ValueError(str(exc)) from exc
    if lock_path is not None:
        config = lock_rescore_config(read_lock(lock_path))
        return _narrowed(config, request.scorer_refs), lock_path
    found = run_assessment_config(request.target)
    if found is None:
        return RescoreConfig(), None
    return _narrowed(load_rescore_config(found), request.scorer_refs), found


def _rescore(request: _RescoreRequest, out: Path | None) -> None:
    store = request.store
    if store is None:
        raise click.UsageError("--rescore needs --store DIR (kept outside the job directory)")
    if store.resolve().is_relative_to(request.target.resolve()):
        raise click.UsageError("--store must not be inside the job being rescored")
    _check_as1(request.target, request.lock, request.scorer_refs, request.bundle)
    options = RegradeOptions(docker_context=request.docker_context, bundle_override=request.bundle)
    graded = grade_pre_fix(
        request.target, store, request.scorer_refs, options, replay=request.replay
    )
    covered = produce_coverage(
        request.target, store, request.scorer_refs, options, replay=request.replay
    )
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
    report = {"config": str(config_path) if config_path else None, **report_payload(records)}
    _emit(
        {
            **report,
            **({"pre_fix_grading": graded} if graded is not None else {}),
            **({"offline_coverage": covered} if covered is not None else {}),
        },
        out,
    )


@click.command()
@click.option(
    "--regrade",
    "regrade_target",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Trial or job directory whose stored snapshots the verifier regrades.",
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
    help=(
        "Assessment store directory for --rescore (outside the job); with --regrade, also "
        "regrade its stored pre-fix grades."
    ),
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help=(
        "With --rescore, YAML with inventory_policy and per-scorer config (scorers: {ref: {}}); "
        "default: the assessment settings of the job's experiment lock."
    ),
)
@click.option(
    "--lock",
    "lock_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="With --rescore, the experiment lock whose assessment settings apply "
    "(default: the job's experiment lock, else its job.lock.json).",
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
    help="Verifier bundle location if it moved (its digest must still match the records).",
)
@click.option(
    "--coverage-check",
    is_flag=True,
    help=(
        "With --regrade, instead check the offline coverage pass: make every step's coverage "
        "run from its stored snapshot and compare it with the trial's own coverage pass (or "
        "the --store run of a deferred step); writes nothing."
    ),
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
    lock_path: Path | None,
    replay: bool,
    jev_replay_only: bool,
    bundle_override: Path | None,
    coverage_check: bool,
    docker_context: str,
    out: Path | None,
) -> None:
    """Re-assess stored jobs without re-running agents."""
    if coverage_check and regrade_target is None:
        raise click.UsageError("--coverage-check applies to --regrade only")
    if regrade_target is not None and rescore_target is None:
        _check_as1(regrade_target, lock_path, (), bundle_override)
        options = RegradeOptions(docker_context=docker_context, bundle_override=bundle_override)
        _regrade(regrade_target, options, out, store, coverage_check=coverage_check)
    elif rescore_target is not None and regrade_target is None:
        request = _RescoreRequest(
            rescore_target,
            scorer_refs,
            store,
            config_path,
            bundle_override,
            replay,
            jev_replay_only,
            lock_path,
            docker_context,
        )
        _rescore(request, out)
    else:
        raise click.UsageError("choose one assessment mode: --regrade PATH or --rescore PATH")
