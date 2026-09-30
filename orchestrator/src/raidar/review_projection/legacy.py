"""Scenario-epoch read path: persisted ``review-projection.json`` -> the frozen projection.

Old-epoch experiments persisted ``review-projection.json`` beside their runs.
The scenario-epoch producer (scenario runner, ``EvalRun`` scorecards) was
removed at the WS-L cutover; this module only reloads those artifacts, so
historical experiments keep rendering in the review surface.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import ValidationError

from raidar.schemas.review import ExperimentReviewProjection

EXPERIMENT_PROJECTION_FILENAME = "review-projection.json"


def load_experiment_projection(path: Path) -> ExperimentReviewProjection:
    try:
        return ExperimentReviewProjection.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ValueError(f"invalid review projection {path}: {exc}") from exc


def load_experiment_projections(experiments_root: Path) -> list[ExperimentReviewProjection]:
    """Every persisted projection that still validates; the others are named on stderr.

    A historical projection that no longer matches the frozen schema cannot
    render, so it is skipped whole (never partly rendered) and one bad
    experiment does not hide the rest of the history.
    """
    projections: list[ExperimentReviewProjection] = []
    experiment_dirs = sorted(path for path in experiments_root.iterdir() if path.is_dir())
    for experiment_dir in experiment_dirs:
        projection_path = experiment_dir / EXPERIMENT_PROJECTION_FILENAME
        if projection_path.is_file():
            try:
                projections.append(load_experiment_projection(projection_path))
            except ValueError as exc:
                first_line = str(exc).splitlines()[0]
                print(
                    f"Skipping legacy experiment that fails the schema: {first_line}",
                    file=sys.stderr,
                )
        else:
            print(
                f"Skipping legacy experiment without {EXPERIMENT_PROJECTION_FILENAME}: "
                f"{experiment_dir.name}",
                file=sys.stderr,
            )
    return projections
