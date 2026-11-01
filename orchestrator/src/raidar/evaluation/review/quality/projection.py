"""Build and write the Quality projection (``python -m raidar.evaluation.review.quality``).

The review surface's ``scripts/build-quality-data.mjs`` validates and copies
the written JSON; it owns no scoring or status interpretation.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from raidar_contract.canonical import pretty_canonical_json_bytes

from raidar.evaluation.review.integrity import check_review_jobs
from raidar.evaluation.review.quality.experiment_view import ReviewedTrial, build_experiments
from raidar.evaluation.review.quality.gate_view import GateReader, read_gate_from_disk
from raidar.evaluation.review.quality.labels import JevAudit
from raidar.evaluation.review.quality.models import QualityProjection
from raidar.evaluation.review.quality.run_view import build_run_detail
from raidar.evaluation.review.quality.sources import (
    StoreIndex,
    TrialSource,
    discover_trials,
)
from raidar.scoring.catalog.outcomes import (
    CODE_QUALITY_REF,
    CORRECTNESS_REF,
    VERIFICATION_EFFORT_REF,
)
from raidar.scoring.framework.store import Assessment


def build_quality_projection(
    jobs_root: Path, store_root: Path, *, synthetic: bool = False, locks: Sequence[Path] = ()
) -> QualityProjection:
    """Every trial under ``jobs_root`` with its store Assessments and Jev decisions."""
    return build_quality_projection_from(
        [(jobs_root, store_root)], synthetic=synthetic, locks=locks
    )


def review_trials(
    trials: Sequence[TrialSource],
    find: Callable[[str, TrialSource], Assessment | None],
    audit: JevAudit,
    gate_reader: GateReader = read_gate_from_disk,
) -> list[ReviewedTrial]:
    """Each trial's run detail from the Assessments ``find`` returns and the Jev ``audit``."""
    attached = audit.attach(list(trials))
    reviewed = []
    for trial in trials:
        jev = attached[trial.review_id]
        detail = build_run_detail(
            trial,
            find(CODE_QUALITY_REF, trial),
            find(CORRECTNESS_REF, trial),
            decisions=jev.decisions,
            labels=audit.labels,
            jev_note=jev.note,
            gate=find(VERIFICATION_EFFORT_REF, trial),
            gate_reader=gate_reader,
        )
        reviewed.append(ReviewedTrial(source=trial, detail=detail))
    return reviewed


def _reviewed(jobs_root: Path, store_root: Path, locks: Sequence[Path]) -> list[ReviewedTrial]:
    return review_trials(
        discover_trials(jobs_root, locks),
        StoreIndex.open(store_root).find,
        JevAudit.open(store_root),
    )


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
    integrity = check_review_jobs([item.source.job for item in reviewed if item.source.job])
    return assemble_projection(
        reviewed, generated_from=_generated_from(sources), synthetic=synthetic, integrity=integrity
    )


def assemble_projection(
    reviewed: Sequence[ReviewedTrial],
    *,
    generated_from: str,
    synthetic: bool,
    integrity: Mapping[Path, str],
) -> QualityProjection:
    """The projection of reviewed trials, each run's integrity note (by job dir) appended."""
    _require_unique_ids(reviewed)
    noted = [
        replace(
            item,
            detail=item.detail.model_copy(
                update={"notes": (*item.detail.notes, integrity[item.source.job_dir.resolve()])}
            ),
        )
        if item.source.job_dir.resolve() in integrity
        else item
        for item in reviewed
    ]
    return QualityProjection(
        generated_from=generated_from,
        synthetic=synthetic,
        runs=tuple(item.detail.run for item in noted),
        run_details={item.detail.run.id: item.detail for item in noted},
        experiments=build_experiments(noted),
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
        "beside each run's experiment file",
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
