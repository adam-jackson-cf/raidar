"""Read jobs, their lock and the Assessment store into ``AnalysisInputs``.

Everything is read-only: job directories and the Assessment store are never
written. ``raidar.analysis.sources`` reads and checks the jobs and the lock;
``raidar.analysis.lookup`` finds each trial's Assessments.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.analysis.assessed import (
    ANALYSED_SCORERS,
    GATE_EFFORT_REF,
    PROCESS_COMPLIANCE_REFS,
    build_outcome,
)
from raidar.analysis.lookup import AssessmentLookup, assessment_config
from raidar.analysis.manipulation import CheckRequest
from raidar.analysis.report import AnalysisInputs, DevRun
from raidar.analysis.sources import (
    AnalysisInputError,
    DevOptions,
    check_jobs,
    default_lock,
    job_options,
    planned_trials,
    read_job,
    read_lock,
    select_records,
)
from raidar.execution.assessment import lock_errata
from raidar.scorers.catalog import ScorerRegistry
from raidar.scorers.store import AssessmentStore
from raidar.scorers.trial_inputs import BundleCache
from raidar.spec.metrics import DEFAULT_METRIC_REGISTRY, Metric


def headline(lock: Mapping[str, Any]) -> tuple[Metric, dict[str, object]]:
    declared = lock["assessment"].get("metric")
    if not isinstance(declared, dict) or "ref" not in declared:
        raise AnalysisInputError("the lock names no headline metric")
    return DEFAULT_METRIC_REGISTRY.get(str(declared["ref"])), dict(declared)


def _dev_job(options: DevOptions) -> DevRun | None:
    if not options.active:
        return None
    return DevRun(
        max_checkpoints=options.max_checkpoints,
        agent_override=options.agent_override,
        note=options.describe(),
    )


@dataclass(frozen=True)
class LoadRequest:
    job_dirs: tuple[Path, ...]
    store_root: Path
    registry: ScorerRegistry
    lock_path: Path | None = None
    assessment_config: Path | None = None
    allow_dev_jobs: bool = False
    bundle_search: tuple[Path, ...] = ()


def _process_ref(declared: tuple[str, ...]) -> str | None:
    """The process-compliance version the lock declares (``@2`` first), if any."""
    return next((ref for ref in PROCESS_COMPLIANCE_REFS if ref in declared), None)


def _scorer_refs(declared: tuple[str, ...], process_ref: str | None) -> tuple[str, ...]:
    wanted = (*ANALYSED_SCORERS, *((process_ref,) if process_ref else ()), GATE_EFFORT_REF)
    return tuple(ref for ref in wanted if ref in declared)


def manipulation_requests(analysis: Mapping[str, Any]) -> tuple[CheckRequest, ...] | None:
    """The lock's pre-registered manipulation checks, or None when it declares none."""
    declared = analysis.get("manipulation_checks")
    if not declared:
        return None
    return tuple(CheckRequest.model_validate(item) for item in declared)


def load_inputs(request: LoadRequest) -> tuple[AnalysisInputs, Mapping[str, Any]]:
    """The analysis inputs and the lock they were checked against."""
    if not request.job_dirs:
        raise AnalysisInputError("choose at least one job directory")
    jobs = [read_job(path) for path in request.job_dirs]
    lock = read_lock(request.lock_path or default_lock(jobs[0]))
    check_jobs(jobs, lock)
    options = job_options(jobs, allow_dev_jobs=request.allow_dev_jobs)
    policy, policy_facts = headline(lock)
    spec = lock["spec"]
    declared = tuple(spec["assessment"]["scorers"])
    process_ref = _process_ref(declared)
    lookup = AssessmentLookup(
        store=AssessmentStore(request.store_root),
        registry=request.registry,
        scorer_refs=_scorer_refs(declared, process_ref),
        bundles=BundleCache(search=request.bundle_search),
    )
    chosen, replaced = select_records(jobs)
    outcomes, process, gate = [], {}, {}
    gated = GATE_EFFORT_REF in declared
    for trial_id, (job, record) in sorted(chosen.items()):
        config = assessment_config(job, request.assessment_config, lock)
        results = lookup.valid_results(
            job.trial_dir(record), config, trial_id=record.trial.trial_id, valid=record.valid
        )
        outcomes.append(build_outcome(record, results, policy=policy))
        process[trial_id] = results.get(process_ref or "")
        gate[trial_id] = results.get(GATE_EFFORT_REF)
    inputs = AnalysisInputs(
        experiment=str(lock["experiment"]["id"]),
        lock_sha256=str(lock["lock_sha256"]),
        errata={ref: errata.model_dump(mode="json") for ref, errata in lock_errata(lock).items()},
        jobs=tuple(job.job_id for job in jobs),
        kind=str(spec.get("kind", "ab-test")),
        rank_by=spec["analysis"].get("rank_by"),
        attempts=int(spec["analysis"]["attempts"]),
        metric=policy_facts,
        baseline_arm=str(spec["analysis"]["baseline_arm"]),
        progression=str(spec["protocol"]["progression"]),
        planned=planned_trials(jobs[0].plan),
        outcomes=tuple(outcomes),
        replaced=replaced,
        process_results=process if process_ref else None,
        gate_results=gate if gated else None,
        manipulation_checks=manipulation_requests(spec["analysis"]),
        dev_job=_dev_job(options),
    )
    return inputs, lock
