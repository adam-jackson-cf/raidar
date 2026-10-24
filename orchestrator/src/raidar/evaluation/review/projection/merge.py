"""Project runs into the one ``schema_version=2`` artifact the surface build copies.

Experiment runs (``--jobs-root``, scored from ``--store``) are projected from
their artifacts on every build; nothing is persisted per experiment.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from raidar.contract.review import (
    ArmDiffArtifact,
    ReviewExperimentRecord,
    ReviewProjectionArtifact,
    ReviewRunDetail,
)
from raidar.evaluation.review.projection.assessments import AssessmentLookup
from raidar.evaluation.review.projection.comparability import mark_policy_mismatches
from raidar.evaluation.review.projection.experiment_epoch import EpochProjection, project_sources
from raidar.evaluation.review.projection.sources import (
    JobSourceError,
    LockIndex,
    discover_job_dirs,
    load_job,
)
from raidar.evaluation.review.projection.spans import repo_path
from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.framework.store import AssessmentStore


def project_experiment_epoch(
    job_roots: Sequence[Path], stores: Sequence[Path] = (), locks: Sequence[Path] = ()
) -> EpochProjection:
    index = LockIndex(locks)
    sources = []
    for job_dir in discover_job_dirs(job_roots):
        try:
            source = load_job(job_dir, index)
        except JobSourceError as exc:
            # One unprojectable run (e.g. its lock was overwritten) never aborts the build.
            print(f"Skipping run, lock unavailable: {repo_path(job_dir)}: {exc}", file=sys.stderr)
            continue
        if source.records:
            sources.append(source)
        else:
            print(f"Skipping run without trial records: {repo_path(job_dir)}", file=sys.stderr)
    lookup = AssessmentLookup([AssessmentStore(path) for path in stores], default_scorer_registry())
    return project_sources(sources, lookup)


def _unique_details(details: Sequence[ReviewRunDetail]) -> list[ReviewRunDetail]:
    run_ids = [detail.run.id for detail in details]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("review projection contains duplicate job ids")
    return list(details)


def merge_review_projections(
    *,
    job_roots: Sequence[Path] = (),
    stores: Sequence[Path] = (),
    locks: Sequence[Path] = (),
) -> ReviewProjectionArtifact:
    """Project the runs under ``job_roots`` and flag families that mix headline metrics."""
    epoch = project_experiment_epoch(job_roots, stores, locks)
    if not epoch.experiments:
        where = ", ".join(str(root) for root in job_roots) or "no source"
        raise ValueError(f"no projectable runs found under {where}")
    experiments, diffs = mark_policy_mismatches(epoch.experiments, epoch.diffs)
    return _artifact(job_roots, experiments, _unique_details(epoch.details), diffs)


def _artifact(
    roots: Sequence[Path],
    experiments: Sequence[ReviewExperimentRecord],
    details: Sequence[ReviewRunDetail],
    diffs: Sequence[ArmDiffArtifact],
) -> ReviewProjectionArtifact:
    return ReviewProjectionArtifact(
        generated_from=", ".join(repo_path(root) for root in roots),
        experiments=list(experiments),
        runs=sorted((detail.run for detail in details), key=lambda run: (-run.started_at, run.id)),
        run_details={detail.run.id: detail for detail in details},
        arm_diffs=list(diffs),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project Raidar runs into review-surface data.")
    parser.add_argument(
        "--jobs-root",
        type=Path,
        action="append",
        default=[],
        help="Experiment-epoch jobs root (<experiment>/<job>/run.json); repeatable.",
    )
    parser.add_argument(
        "--store",
        type=Path,
        action="append",
        default=[],
        help="Assessment store holding the runs' Assessments; repeatable.",
    )
    parser.add_argument(
        "--lock",
        type=Path,
        action="append",
        default=[],
        help="Experiment lock file for runs whose lock is not beside their experiment file.",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if not args.jobs_root:
        raise SystemExit("give at least one --jobs-root")
    artifact = merge_review_projections(
        job_roots=[path.resolve() for path in args.jobs_root],
        stores=[path.resolve() for path in args.store],
        locks=[path.resolve() for path in args.lock],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(
        f"Projected {len(artifact.runs)} run(s) across {len(artifact.experiments)} "
        f"experiment(s) into {args.output}"
    )
