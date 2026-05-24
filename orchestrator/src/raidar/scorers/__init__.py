"""Scorer definition registry and resolution helpers."""

from .base import BaseScorer, ScorerContext, ScorerEvidence
from .code_task import CodeTaskScorer
from .registry import (
    ResolvedScorer,
    ScorerDefinition,
    ScorerResolutionError,
    load_scorer_definition,
    resolve_scorers,
    resolved_metrics,
    scenario_scorers,
    scorer_evaluation_profile,
)

__all__ = [
    "BaseScorer",
    "CodeTaskScorer",
    "ResolvedScorer",
    "ScorerContext",
    "ScorerDefinition",
    "ScorerEvidence",
    "ScorerResolutionError",
    "load_scorer_definition",
    "resolve_scorers",
    "resolved_metrics",
    "scorer_evaluation_profile",
    "scenario_scorers",
]
