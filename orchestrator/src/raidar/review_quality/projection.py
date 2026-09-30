"""Build and write the Quality projection (``python -m raidar.review_quality``).

The review surface's ``scripts/build-quality-data.mjs`` validates and copies
the written JSON; it owns no scoring or status interpretation.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from raidar.review_quality.experiment_view import ReviewedTrial, build_experiments
from raidar.review_quality.models import QualityProjection
from raidar.review_quality.run_view import build_run_detail
from raidar.review_quality.sources import (
    CODE_QUALITY_REF,
    CORRECTNESS_REF,
    JevAudit,
    StoreIndex,
    discover_trials,
)
from raidar.spec.canonical import pretty_canonical_json_bytes


def build_quality_projection(
    runs_root: Path, store_root: Path, *, synthetic: bool = False
) -> QualityProjection:
    """Every trial under ``runs_root`` with its store Assessments and Jev decisions."""
    return build_quality_projection_from([(runs_root, store_root)], synthetic=synthetic)


def _reviewed(runs_root: Path, store_root: Path) -> list[ReviewedTrial]:
    store = StoreIndex.open(store_root)
    audit = JevAudit.open(store_root)
    trials = discover_trials(runs_root)
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
        )
        reviewed.append(ReviewedTrial(source=trial, detail=detail))
    return reviewed


def build_quality_projection_from(
    sources: Sequence[tuple[Path, Path]], *, synthetic: bool = False
) -> QualityProjection:
    """Every trial of each ``(runs_root, store)`` pair, each read against its own store.

    Experiments are grouped across all pairs (runs under one lock are one
    experiment wherever they sit), and review ids must stay unique across them.
    """
    if not sources:
        raise ValueError("at least one runs root and store are required")
    reviewed = [item for runs_root, store in sources for item in _reviewed(runs_root, store)]
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
    duplicates = sorted({run_id for run_id in ids if ids.count(run_id) > 1})
    if duplicates:
        raise ValueError(f"trials share review ids: {duplicates}")


def _generated_from(sources: Sequence[tuple[Path, Path]]) -> str:
    return "; ".join(f"{_portable(runs_root)} + {_portable(store)}" for runs_root, store in sources)


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
        "--runs-root",
        type=Path,
        action="append",
        required=True,
        help="Directory holding run dirs (repeatable; paired with --store by position)",
    )
    parser.add_argument(
        "--store",
        type=Path,
        action="append",
        required=True,
        help="Assessment store directory for the --runs-root at the same position",
    )
    parser.add_argument("--output", type=Path, required=True, help="Projection JSON to write")
    parser.add_argument(
        "--synthetic", action="store_true", help="Mark the projection as synthetic fixture data"
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    if len(args.runs_root) != len(args.store):
        parser.error(
            f"{len(args.runs_root)} --runs-root but {len(args.store)} --store: "
            "give one store per runs root, in the same order"
        )
    projection = build_quality_projection_from(
        list(zip(args.runs_root, args.store, strict=True)), synthetic=args.synthetic
    )
    write_quality_projection(projection, args.output)
    print(
        f"Projected {len(projection.runs)} run(s) across "
        f"{len(projection.experiments)} experiment(s) into {args.output}"
    )
