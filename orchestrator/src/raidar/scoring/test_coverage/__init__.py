"""``test-coverage``: recorded Jev readings of how well final mapped tests exercise criteria."""

from raidar.scoring.test_coverage.manifest import TEST_COVERAGE
from raidar.scoring.test_coverage.scorer import TestCoverageScorer, make_test_coverage_scorer

__all__ = ["TEST_COVERAGE", "TestCoverageScorer", "make_test_coverage_scorer"]
