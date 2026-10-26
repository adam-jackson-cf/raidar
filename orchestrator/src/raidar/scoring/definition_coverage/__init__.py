"""``definition-coverage``: recorded Jev readings of how fully a definition captures the issue."""

from raidar.scoring.definition_coverage.manifest import DEFINITION_COVERAGE
from raidar.scoring.definition_coverage.scorer import (
    DefinitionCoverageScorer,
    definition_coverage_scorer,
)

__all__ = ["DEFINITION_COVERAGE", "DefinitionCoverageScorer", "definition_coverage_scorer"]
