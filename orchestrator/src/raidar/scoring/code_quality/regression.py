"""Pure regression-breadth (G) attribution kernel.

Ported from ``slop-code-bench`` ``src/slop_code/metrics/scoring/regression.py``
at ``25069ba`` (``REGRESSION_CONTRACT`` below). G needs exact parity between a
baseline run and the coverage-measured run (environment, corpus, invocation,
outcome and process ledgers); without it the transition is ineligible
(``coverage_parity_unavailable``), never zero. With parity, each failing
canonical regression node is attributed through its per-test coverage to the
innermost changed production symbol, and
``G = 1 - affected_changed_symbols / all_changed_symbols`` (1 when nothing
changed).
"""

from __future__ import annotations

from decimal import Decimal

from raidar.scoring.code_quality.lineage_models import (
    RegressionAttribution,
    RegressionBreadthInput,
    RegressionOutcome,
    RegressionPhase,
    RegressionRunEvidence,
    RegressionSymbolAttribution,
    SymbolIdentity,
)
from raidar.scoring.code_quality.models import Eligibility, canonical_decimal

REGRESSION_CONTRACT = {
    "environment": "exact_environment_id_project_lock_plugin_interpreter_sha256",
    "parity": "exact_environment_corpus_invocation_node_phase_outcome_ledger",
    "coverage": "contexts_join_exact_parametrized_node_id",
    "attribution": "failing_canonical_regression_to_innermost_changed_symbol",
    "deduplication": "node_path_line_symbol_stable_identity",
    "process_audit": "failing_regression_process_event_requires_node_coverage",
    "regression": "1-affected_changed_symbols/all_changed_symbols;one_when_none",
}
PARITY_UNAVAILABLE = "coverage_parity_unavailable"
_FAILING = (RegressionOutcome.FAILED, RegressionOutcome.ERROR)
_LedgerRow = tuple[str, str, str]


def _outcome_ledger(run: RegressionRunEvidence) -> tuple[_LedgerRow, ...]:
    return tuple((item.node_id, item.phase.value, item.outcome.value) for item in run.outcomes)


def _ledger(run: RegressionRunEvidence) -> tuple[_LedgerRow, ...]:
    return tuple((item.node_id, item.phase.value, item.outcome.value) for item in run.ledger)


def _process_ledger(run: RegressionRunEvidence) -> tuple[_LedgerRow, ...]:
    return tuple((item.node_id, item.phase.value, item.event) for item in run.process_events)


def _ineligible_attribution(evidence: RegressionBreadthInput) -> RegressionAttribution:
    return RegressionAttribution(
        input=evidence,
        attributions=(),
        eligibility=Eligibility(eligible=False, reasons=(PARITY_UNAVAILABLE,)),
        regression=None,
    )


def _coverage_nodes(run: RegressionRunEvidence) -> frozenset[str]:
    return frozenset(item.node_id for item in run.coverage)


def _parity_available(evidence: RegressionBreadthInput) -> bool:
    """Require exact run identity and complete, internally consistent ledgers."""
    baseline = evidence.baseline
    produced = evidence.produced
    if (
        baseline.environment != produced.environment
        or baseline.corpus != produced.corpus
        or baseline.invocation != produced.invocation
    ):
        return False
    baseline_outcomes = _outcome_ledger(baseline)
    produced_outcomes = _outcome_ledger(produced)
    if (
        baseline_outcomes != _ledger(baseline)
        or produced_outcomes != _ledger(produced)
        or baseline_outcomes != produced_outcomes
        or _process_ledger(baseline) != _process_ledger(produced)
    ):
        return False
    outcome_nodes = frozenset(item.node_id for item in produced.outcomes)
    return _coverage_nodes(produced) <= outcome_nodes


def _innermost_symbol(
    symbols: tuple[SymbolIdentity, ...], path: str, line: int
) -> SymbolIdentity | None:
    candidates = tuple(
        symbol
        for symbol in symbols
        if symbol.path == path and symbol.start_line <= line <= symbol.end_line
    )
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda symbol: (
            symbol.end_line - symbol.start_line,
            -symbol.start_line,
            symbol.qualified_name,
            symbol.kind,
        ),
    )


def _failed_regression_nodes(evidence: RegressionBreadthInput) -> frozenset[str]:
    return frozenset(
        item.node_id
        for item in evidence.produced.outcomes
        if item.group_type == "Regression" and item.outcome in _FAILING
    )


def _unattributable_process(evidence: RegressionBreadthInput, failed: frozenset[str]) -> bool:
    coverage_nodes = _coverage_nodes(evidence.produced)
    return any(
        event.phase is RegressionPhase.COLLECTION
        or (event.node_id in failed and event.node_id not in coverage_nodes)
        for event in evidence.produced.process_events
    )


def _attributions(
    evidence: RegressionBreadthInput, failed: frozenset[str]
) -> tuple[RegressionSymbolAttribution, ...]:
    found = (
        RegressionSymbolAttribution(
            node_id=context.node_id, path=context.path, line=line, symbol=symbol
        )
        for context in evidence.produced.coverage
        if context.node_id in failed
        for line in context.executed_lines
        if (symbol := _innermost_symbol(evidence.changed_symbols, context.path, line)) is not None
    )
    unique = {
        (item.node_id, item.path, item.line, item.symbol.stable_identity): item for item in found
    }
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.node_id.encode("utf-8"),
                item.path.encode("utf-8"),
                item.line,
                item.symbol.stable_identity,
            ),
        )
    )


def calculate_regression_breadth(evidence: RegressionBreadthInput) -> RegressionAttribution:
    """Calculate G from failing canonical regressions and changed symbols only.

    Exact parity is required. Process-created failing regressions are attributable
    only when the locked measurement includes coverage for their canonical nodes.
    """
    if not _parity_available(evidence):
        return _ineligible_attribution(evidence)
    failed = _failed_regression_nodes(evidence)
    if _unattributable_process(evidence, failed):
        return _ineligible_attribution(evidence)
    attributions = _attributions(evidence, failed)
    changed = {symbol.stable_identity for symbol in evidence.changed_symbols}
    affected = {item.symbol.stable_identity for item in attributions}
    regression = (
        Decimal("1")
        if not changed
        else Decimal("1") - (Decimal(len(affected)) / Decimal(len(changed)))
    )
    return RegressionAttribution(
        input=evidence,
        attributions=attributions,
        eligibility=Eligibility(eligible=True),
        regression=canonical_decimal(regression),
    )
