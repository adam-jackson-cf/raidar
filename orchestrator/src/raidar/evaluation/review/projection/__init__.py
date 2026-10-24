"""Review-surface projection producer (``schema_version=2`` contract).

The surface reads one Python-emitted projection; this package is its only
producer. ``experiment_epoch`` projects experiment runs (problems, arms,
Assessments, ATIF traces); ``merge`` assembles the artifact and is the
``python -m raidar.evaluation.review.projection`` entrypoint.
"""

from raidar.evaluation.review.projection.merge import main, merge_review_projections

__all__ = [
    "main",
    "merge_review_projections",
]
