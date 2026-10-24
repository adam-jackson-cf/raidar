"""``code-quality`` scorer: the SCB fork's quality scoring core (plan WS-G G2/G3).

- Ported kernels (pure, golden-tested against the fork): ``models`` and
  ``lineage_models`` (typed evidence), ``quality`` (V/E), ``architecture``
  (H), ``lineage`` (R kernel and ledger), ``regression`` (G attribution),
  ``arithmetic`` (problem-level means, composed with ``scb-canonical@1``).
- Python measurement adapters (G3): ``snapshot_tool`` runs inside
  the pinned ``scb-python-quality`` tool env (tree-sitter symbols and hashes,
  call graph, import tracing, radon file rows, evaluator token/AST/clone
  semantics); ``measurement`` runs it and Ruff; ``diffs`` compares adjacent
  snapshots.
- ``scorer`` assembles checkpoint, transition and problem observations with
  ``file:line`` evidence for the Assessment store (WS-F).
"""

from raidar.scoring.code_quality.scorer import (
    CodeQualityConfig,
    CodeQualityScorer,
    code_quality_scorer,
    code_quality_scorer_for_store,
)

__all__ = [
    "CodeQualityConfig",
    "CodeQualityScorer",
    "code_quality_scorer",
    "code_quality_scorer_for_store",
]
