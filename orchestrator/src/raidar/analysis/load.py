"""Read runs, their lock and the Assessment store into ``AnalysisInputs``.

Everything is read-only: run directories and the Assessment store are never
written. ``raidar.analysis.sources`` reads and checks the runs and the lock;
``raidar.analysis.lookup`` finds each trajectory's Assessments.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.analysis.assessed import ANALYSED_SCORERS, PROCESS_COMPLIANCE_REF, build_outcome
from raidar.analysis.lookup import AssessmentLookup, assessment_config
from raidar.analysis.report import AnalysisInputs, DevRun
from raidar.analysis.sources import (
    AnalysisInputError,
    DevOptions,
    check_runs,
    default_lock,
    planned_trials,
    read_lock,
    read_run,
    run_options,
    select_records,
)
from raidar.scorers.catalog import ScorerRegistry
from raidar.scorers.store import AssessmentStore
from raidar.scorers.trial_inputs import BundleCache
from raidar.spec.policies import DEFAULT_POLICY_REGISTRY, ScorePolicy


def headline(lock: Mapping[str, Any]) -> tuple[ScorePolicy, dict[str, object]]:
    declared = lock["assessment"].get("headline_policy")
    if not isinstance(declared, dict) or "ref" not in declared:
        raise AnalysisInputError("the lock names no headline_policy")
    return DEFAULT_POLICY_REGISTRY.get(str(declared["ref"])), dict(declared)


def _dev_run(options: DevOptions) -> DevRun | None:
    if not options.active:
        return None
    return DevRun(
        max_checkpoints=options.max_checkpoints,
        agent_override=options.agent_override,
        note=options.describe(),
    )


@dataclass(frozen=True)
class LoadRequest:
    run_dirs: tuple[Path, ...]
    store_root: Path
    registry: ScorerRegistry
    lock_path: Path | None = None
    assessment_config: Path | None = None
    allow_dev_runs: bool = False


def load_inputs(request: LoadRequest) -> tuple[AnalysisInputs, Mapping[str, Any]]:
    """The analysis inputs and the lock they were checked against."""
    if not request.run_dirs:
        raise AnalysisInputError("choose at least one run directory")
    runs = [read_run(path) for path in request.run_dirs]
    lock = read_lock(request.lock_path or default_lock(runs[0]))
    check_runs(runs, lock)
    options = run_options(runs, allow_dev_runs=request.allow_dev_runs)
    policy, policy_facts = headline(lock)
    spec = lock["spec"]
    declared = tuple(spec["assessment"]["scorers"])
    wanted = (*ANALYSED_SCORERS, PROCESS_COMPLIANCE_REF)
    lookup = AssessmentLookup(
        store=AssessmentStore(request.store_root),
        registry=request.registry,
        scorer_refs=tuple(ref for ref in wanted if ref in declared),
        bundles=BundleCache(),
    )
    chosen, replaced = select_records(runs)
    outcomes, process = [], {}
    for trial_id, (run, record) in sorted(chosen.items()):
        config = assessment_config(run, request.assessment_config)
        results = lookup.results(run.trial_dir(record), config)
        outcomes.append(build_outcome(record, results, policy=policy))
        process[trial_id] = results.get(PROCESS_COMPLIANCE_REF)
    inputs = AnalysisInputs(
        experiment=str(lock["experiment"]["id"]),
        lock_sha256=str(lock["lock_sha256"]),
        runs=tuple(run.run_id for run in runs),
        headline_policy=policy_facts,
        baseline_arm=str(spec["analysis"]["baseline_arm"]),
        progression=str(spec["protocol"]["progression"]),
        planned=planned_trials(runs[0].plan),
        outcomes=tuple(outcomes),
        replaced=replaced,
        process_results=process if PROCESS_COMPLIANCE_REF in declared else None,
        dev_run=_dev_run(options),
    )
    return inputs, lock
