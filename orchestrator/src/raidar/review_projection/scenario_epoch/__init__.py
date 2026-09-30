"""Scenario-epoch history: read-only. No producer exists; do not extend.

Experiments run before the reboot cutover persisted a ``review-projection.json``
each (``experiments/benchmarks/<experiment>/`` on a real checkout). The
scenario runner that wrote them is gone, so this subpackage only reloads them
(``reader``) and writes the frozen, labelled synthetic copies the review-surface
suite asserts against (``fixture``). It imports nothing but
``raidar.schemas.review``, and only ``raidar.review_projection.merge`` and the
experiment fixture import it (``orchestrator/.importlinter``). The wire fields
it validates (``scenario``, ``revision``, ``scenario_meta``, ``revision_diffs``)
are the frozen ``schema_version=1`` contract and stay as they are.
"""

from raidar.review_projection.scenario_epoch.reader import (
    EXPERIMENT_PROJECTION_FILENAME,
    load_experiment_projection,
    load_experiment_projections,
)

__all__ = [
    "EXPERIMENT_PROJECTION_FILENAME",
    "load_experiment_projection",
    "load_experiment_projections",
]
