"""Comparison and aggregation modules for evaluation results."""

from .aggregator import ResultsAggregator, aggregate_results
from .matrix_runner import MatrixRunner, run_matrix

__all__ = [
    "ResultsAggregator",
    "aggregate_results",
    "MatrixRunner",
    "run_matrix",
]
