"""Build and write the Quality projection (``python -m raidar.review.quality``).

The review surface's ``scripts/build-quality-data.mjs`` validates and copies
the written JSON; it owns no scoring or status interpretation.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from raidar.review.quality.experiment_view import ReviewedTrial, build_experiments
from raidar.review.quality.gate_view import GATE_EFFORT_REF
from raidar.review.quality.models import QualityProjection
from raidar.review.quality.run_view import build_run_detail
from raidar.review.quality.sources import (
    CODE_QUALITY_REF,
    CORRECTNESS_REF,
    JevAudit,
    StoreIndex,
    discover_trials,
)
from raidar.spec.canonical import pretty_canonical_json_bytes


def build_quality_projection(
    jobs_root: Path, store_root: Path, *, synthetic: bool = False, locks: Sequence[Path] = ()
) -> QualityProjection:
    """Every trial under ``jobs_root`` with its store Assessments and Jev decisions."""
    return build_quality_projection_from(
        [(jobs_root, store_root)], synthetic=synthetic, locks=locks
    )


def _reviewed(jobs_root: Path, store_root: Path, locks: Sequence[Path]) -> list[ReviewedTrial]:
    store = StoreIndex.open(store_root)
    audit = JevAudit.open(store_root)
    trials = discover_trials(jobs_root, locks)
    attached = audit.attach(trials)
    reviewed = []
    for trial in trials:
        jev = attached[trial.review_id]
        detail = build_run_detail(
            trial,
            store.find(CODE_QUALITY_REF, trial),
            store.find(CORRECTNESS_REF, trial),
            decisions=jev.decisions,
            labels=audit.labels,
            jev_note=jev.note,
            gate=store.find(GATE_EFFORT_REF, trial),
        )
        reviewed.append(ReviewedTrial(source=trial, detail=detail))
    return reviewed


def build_quality_projection_from(
    sources: Sequence[tuple[Path, Path]], *, synthetic: bool = False, locks: Sequence[Path] = ()
) -> QualityProjection:
    """Every trial of each ``(jobs_root, store)`` pair, each read against its own store.

    Experiments are grouped across all pairs (runs under one lock are one
    experiment wherever they sit), and review ids must stay unique across them.
    """
    if not sources:
        raise ValueError("at least one jobs root and store are required")
    reviewed = [item for jobs_root, store in sources for item in _reviewed(jobs_root, store, locks)]
    _require_unique_ids(reviewed)
    return QualityProjection(
        generated_from=_generated_from(sources),
        synthetic=synthetic,
        runs=tuple(item.detail.run for item in reviewed),
        run_details={item.detail.run.id: item.detail for item in reviewed},
        experiments=build_experiments(reviewed),
    )


def _require_unique_ids(reviewed: Sequence[ReviewedTrial]) -> None:
    ids = [item.detail.run.id for item in reviewed]
    duplicates = sorted({job_id for job_id in ids if ids.count(job_id) > 1})
    if duplicates:
        raise ValueError(f"trials share review ids: {duplicates}")


def _generated_from(sources: Sequence[tuple[Path, Path]]) -> str:
    return "; ".join(f"{_portable(jobs_root)} + {_portable(store)}" for jobs_root, store in sources)


def _portable(path: Path) -> str:
    """A cwd-relative path when inside the working directory, else the directory name."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return resolved.name


def write_quality_projection(projection: QualityProjection, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(pretty_canonical_json_bytes(projection.model_dump(mode="json")))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project Raidar runs into the Quality view data.")
    parser.add_argument(
        "--jobs-root",
        type=Path,
        action="append",
        required=True,
        help="Directory holding job dirs (repeatable; paired with --store by position)",
    )
    parser.add_argument(
        "--store",
        type=Path,
        action="append",
        required=True,
        help="Assessment store directory for the --jobs-root at the same position",
    )
    parser.add_argument(
        "--lock",
        type=Path,
        action="append",
        default=[],
        help="Experiment lock to match runs against (repeatable); tried before the lock "
        "beside each run's experiment file and the run's own job.lock.json",
    )
    parser.add_argument("--output", type=Path, required=True, help="Projection JSON to write")
    parser.add_argument(
        "--synthetic", action="store_true", help="Mark the projection as synthetic fixture data"
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    if len(args.jobs_root) != len(args.store):
        parser.error(
            f"{len(args.jobs_root)} --jobs-root but {len(args.store)} --store: "
            "give one store per jobs root, in the same order"
        )
    projection = build_quality_projection_from(
        list(zip(args.jobs_root, args.store, strict=True)),
        synthetic=args.synthetic,
        locks=args.lock,
    )
    write_quality_projection(projection, args.output)
    print(
        f"Projected {len(projection.runs)} run(s) across "
        f"{len(projection.experiments)} experiment(s) into {args.output}"
    )
