"""Review-surface projection producer (frozen ``schema_version=1`` contract).

The surface reads one Python-emitted projection; this package is its only
producer. ``experiment_epoch`` projects experiment runs (problems, arms,
Assessments, ATIF traces) and the read-only ``scenario_epoch`` subpackage keeps
scenario-epoch history rendering from its persisted projections. ``merge``
combines both and is the ``python -m raidar.review_projection`` entrypoint.
"""

from raidar.review_projection.merge import main, merge_review_projections

__all__ = [
    "main",
    "merge_review_projections",
]
