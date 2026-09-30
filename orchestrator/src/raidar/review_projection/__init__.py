"""Review-surface projection producer (frozen ``schema_version=1`` contract).

The surface reads one Python-emitted projection; this package is its only
producer. ``experiment_epoch`` projects experiment runs (problems, arms,
Assessments, ATIF traces) and ``legacy`` keeps scenario-epoch experiments
rendering from their persisted projections. ``merge`` combines both and is the
``python -m raidar.review_projection`` entrypoint.
"""

from raidar.review_projection.legacy import (
    EXPERIMENT_PROJECTION_FILENAME,
    load_experiment_projections,
)
from raidar.review_projection.merge import main, merge_review_projections

__all__ = [
    "EXPERIMENT_PROJECTION_FILENAME",
    "load_experiment_projections",
    "main",
    "merge_review_projections",
]
