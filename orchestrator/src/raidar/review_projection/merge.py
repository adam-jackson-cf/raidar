"""Merge both epochs into the one ``schema_version=1`` artifact the surface build copies.

Experiment-epoch runs (``--runs-root``, scored from ``--store``) are projected
from their artifacts on every build; scenario-epoch experiments
(``--legacy-root``) are read, read-only, from the ``review-projection.json``
each one persisted (``raidar.review_projection.scenario_epoch``), so historical
runs keep rendering unchanged.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from raidar.review_projection.assessments import AssessmentLookup
from raidar.review_projection.comparability import mark_policy_mismatches
from raidar.review_projection.experiment_epoch import EpochProjection, project_sources
from raidar.review_projection.scenario_epoch import load_experiment_projections
from raidar.review_projection.sources import (
    LockIndex,
    RunSourceError,
    discover_run_dirs,
    load_run,
)
from raidar.review_projection.spans import repo_path
from raidar.schemas.review import (
    ExperimentReviewProjection,
    ReviewExperimentRecord,
    ReviewProjectionArtifact,
    ReviewRunDetail,
    RevisionDiffArtifact,
)
from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.store import AssessmentStore


def project_experiment_epoch(
    run_roots: Sequence[Path], stores: Sequence[Path] = (), locks: Sequence[Path] = ()
) -> EpochProjection:
    index = LockIndex(locks)
    sources = []
    for run_dir in discover_run_dirs(run_roots):
        try:
            source = load_run(run_dir, index)
        except RunSourceError as exc:
            # One unprojectable run (e.g. its lock was overwritten) never aborts the build.
            print(f"Skipping run, lock unavailable: {repo_path(run_dir)}: {exc}", file=sys.stderr)
            continue
        if source.records:
            sources.append(source)
        else:
            print(f"Skipping run without trajectory records: {repo_path(run_dir)}", file=sys.stderr)
    lookup = AssessmentLookup([AssessmentStore(path) for path in stores], default_scorer_registry())
    return project_sources(sources, lookup)


def _unique_details(details: Sequence[ReviewRunDetail]) -> list[ReviewRunDetail]:
    run_ids = [detail.run.id for detail in details]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("review projection contains duplicate run ids")
    return list(details)


def _legacy(legacy_roots: Sequence[Path]) -> list[ExperimentReviewProjection]:
    return [p for root in legacy_roots for p in load_experiment_projections(root)]


def _legacy_diffs(legacy: Sequence[ExperimentReviewProjection]) -> list[RevisionDiffArtifact]:
    diffs = {p.revision_diff.key: p.revision_diff for p in legacy if p.revision_diff is not None}
    return sorted(diffs.values(), key=lambda diff: diff.key)


def merge_review_projections(
    legacy_roots: Sequence[Path] = (),
    *,
    run_roots: Sequence[Path] = (),
    stores: Sequence[Path] = (),
    locks: Sequence[Path] = (),
) -> ReviewProjectionArtifact:
    """Project experiment-epoch runs and merge persisted scenario-epoch experiments."""
    epoch = project_experiment_epoch(run_roots, stores, locks)
    legacy = _legacy(legacy_roots)
    roots = [*run_roots, *legacy_roots]
    if not epoch.experiments and not legacy:
        where = ", ".join(str(root) for root in roots) or "no source"
        raise ValueError(f"no current-version review projections found under {where}")
    experiments, diffs = mark_policy_mismatches(
        [*epoch.experiments, *(p.experiment for p in legacy)],
        [*epoch.diffs, *_legacy_diffs(legacy)],
    )
    return _artifact(roots, experiments, _merged_details(epoch, legacy), diffs)


def _merged_details(
    epoch: EpochProjection, legacy: Sequence[ExperimentReviewProjection]
) -> list[ReviewRunDetail]:
    return _unique_details([*epoch.details, *(detail for p in legacy for detail in p.runs)])


def _artifact(
    roots: Sequence[Path],
    experiments: Sequence[ReviewExperimentRecord],
    details: Sequence[ReviewRunDetail],
    diffs: Sequence[RevisionDiffArtifact],
) -> ReviewProjectionArtifact:
    return ReviewProjectionArtifact(
        generated_from=", ".join(repo_path(root) for root in roots),
        experiments=list(experiments),
        runs=sorted((detail.run for detail in details), key=lambda run: (-run.started_at, run.id)),
        run_details={detail.run.id: detail for detail in details},
        revision_diffs=list(diffs),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project Raidar runs into review-surface data.")
    parser.add_argument(
        "--legacy-root",
        type=Path,
        action="append",
        default=[],
        help=(
            "Scenario-epoch history root, read-only (review-projection.json per "
            "experiment); repeatable."
        ),
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        action="append",
        default=[],
        help="Experiment-epoch runs root (<experiment>/<run>/run.json); repeatable.",
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
    if not args.legacy_root and not args.runs_root:
        raise SystemExit("give --legacy-root and/or --runs-root")
    artifact = merge_review_projections(
        [path.resolve() for path in args.legacy_root],
        run_roots=[path.resolve() for path in args.runs_root],
        stores=[path.resolve() for path in args.store],
        locks=[path.resolve() for path in args.lock],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(
        f"Projected {len(artifact.runs)} run(s) across {len(artifact.experiments)} "
        f"experiment(s) into {args.output}"
    )
