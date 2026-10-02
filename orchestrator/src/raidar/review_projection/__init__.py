"""Review-surface projection producer (``schema_version=2`` contract).

The surface reads one Python-emitted projection; this package is its only
producer. ``experiment_epoch`` projects experiment runs (problems, arms,
Assessments, ATIF traces); ``merge`` assembles the artifact and is the
``python -m raidar.review_projection`` entrypoint.
"""

from raidar.review_projection.merge import main, merge_review_projections

__all__ = [
    "main",
    "merge_review_projections",
]
