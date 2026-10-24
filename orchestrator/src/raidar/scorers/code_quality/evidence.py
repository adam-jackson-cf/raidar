"""Project the fork-shaped evidence onto ``file:line`` Evidence items.

Every item points into a stored snapshot with the file's sha256, so the
Assessment store's validation can check it (plan F1):

- V: each Ruff SIM diagnostic and each clone occurrence;
- lint_findings: each Ruff SIM diagnostic;
- E: each high-complexity (cc > 10) production function or method;
- H: each cyclic production call, at the caller's definition;
- R: the repeated-rework lines of each lineage symbol, on the side
  (prior or current snapshot) where they changed;
- G: the executed lines of each changed symbol a failing regression reached.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path

from raidar.scorers.code_quality.architecture import ProductionGraphResult
from raidar.scorers.code_quality.lineage_models import (
    RegressionAttribution,
    SymbolIdentity,
    TransitionReworkEvidence,
)
from raidar.scorers.code_quality.quality import ProductionQualityResult
from raidar.scorers.sdk.result import Evidence, Span

LineKey = tuple[str, str, int]


class FileDigests:
    """sha256 of snapshot files, read once per assessment."""

    def __init__(self) -> None:
        self._digests: dict[Path, str] = {}

    def sha256(self, path: Path) -> str:
        if path not in self._digests:
            self._digests[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        return self._digests[path]


def _item(
    files: FileDigests,
    metric: str,
    subject: str,
    root: Path,
    path: str,
    kind: str,
    spans: Iterable[tuple[int, int]],
    message: str,
) -> Evidence:
    return Evidence(
        metric=metric,
        subject=subject,
        path=path,
        sha256=files.sha256(root / path),
        kind=kind,
        spans=tuple(Span(line=start, end_line=end) for start, end in spans),
        message=message,
    )


def ruff_evidence(
    files: FileDigests, metric: str, subject: str, root: Path, quality: ProductionQualityResult
) -> Iterator[Evidence]:
    for diagnostic in quality.evidence.ruff.diagnostics:
        yield _item(
            files,
            metric,
            subject,
            root,
            diagnostic.path,
            f"ruff:{diagnostic.code}",
            [(diagnostic.start[0], diagnostic.end[0])],
            f"Ruff {diagnostic.code} (SCB contract rule set)",
        )


def clone_evidence(
    files: FileDigests, subject: str, root: Path, quality: ProductionQualityResult
) -> Iterator[Evidence]:
    for number, group in enumerate(quality.evidence.clones, start=1):
        for path, start, end in group.occurrences:
            yield _item(
                files,
                "verbosity",
                subject,
                root,
                path,
                "clone",
                [(start, end)],
                f"clone group {number}: {len(group.vector)} repeated statements, "
                f"{len(group.occurrences)} occurrences",
            )


def erosion_evidence(
    files: FileDigests, subject: str, root: Path, quality: ProductionQualityResult
) -> Iterator[Evidence]:
    for symbol in quality.evidence.eligible_symbols:
        if symbol.high_complexity:
            path, name, kind, start, end = symbol.identity
            yield _item(
                files,
                "erosion",
                subject,
                root,
                path,
                "high_complexity",
                [(start, end)],
                f"{kind} {name}: cc={symbol.cc} sloc={symbol.sloc} mass={symbol.mass}",
            )


def architecture_evidence(
    files: FileDigests, subject: str, root: Path, graph: ProductionGraphResult
) -> Iterator[Evidence]:
    for edge in graph.evidence.edges:
        span = graph.spans.get(edge.source)
        if edge.cyclic and span is not None:
            yield _item(
                files,
                "architecture",
                subject,
                root,
                span.path,
                "cyclic_call",
                [(span.line, span.end_line)],
                f"{edge.source} -> {edge.target} (weight {edge.weight}) is in a call cycle",
            )


def _repeated_owners(transition: TransitionReworkEvidence) -> set[SymbolIdentity]:
    """Lineage endpoints whose changed lines repeat earlier modification (kernel rule)."""
    owners: set[SymbolIdentity] = set()
    ambiguous: dict[str, list[tuple[SymbolIdentity, SymbolIdentity]]] = defaultdict(list)
    ambiguous_before: dict[str, bool] = defaultdict(bool)
    for entry in transition.lineage_ledger:
        edge = entry.candidate
        if edge.accepted:
            if entry.prior_modified_before:
                owners.update((edge.prior, edge.current))
        elif edge.ambiguous_set_id is not None:
            ambiguous[edge.ambiguous_set_id].append((edge.prior, edge.current))
            ambiguous_before[edge.ambiguous_set_id] |= entry.prior_modified_before
    for set_id, pairs in ambiguous.items():
        if ambiguous_before[set_id]:
            owners.update(symbol for pair in pairs for symbol in pair)
    return owners


def repeated_line_keys(transition: TransitionReworkEvidence) -> set[LineKey]:
    """The changed lines R counts as repeated, rebuilt from the kernel's own evidence."""
    owners = _repeated_owners(transition)
    keys: set[LineKey] = {
        (line.side, line.path, line.line)
        for line in transition.changed_physical_lines
        if line.owner is not None and line.owner in owners
    }
    if len(keys) != transition.repeated_lines:
        raise ValueError("repeated-rework evidence does not reconcile with the kernel count")
    return keys


def rework_evidence(
    files: FileDigests,
    subject: str,
    prior_root: Path,
    current_root: Path,
    transition: TransitionReworkEvidence,
) -> Iterator[Evidence]:
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for side, path, line in repeated_line_keys(transition):
        grouped[(side, path)].append(line)
    for (side, path), lines in sorted(grouped.items()):
        root = prior_root if side == "removed" else current_root
        yield _item(
            files,
            "rework",
            subject,
            root,
            path,
            f"repeated_rework:{side}",
            [(line, line) for line in sorted(lines)],
            f"{len(lines)} {side} lines re-modify symbols already changed earlier",
        )


def regression_evidence(
    files: FileDigests, subject: str, current_root: Path, attribution: RegressionAttribution
) -> Iterator[Evidence]:
    lines: dict[SymbolIdentity, set[int]] = defaultdict(set)
    nodes: dict[SymbolIdentity, set[str]] = defaultdict(set)
    for item in attribution.attributions:
        lines[item.symbol].add(item.line)
        nodes[item.symbol].add(item.node_id)
    for symbol in sorted(lines, key=lambda value: value.stable_identity):
        yield _item(
            files,
            "regression",
            subject,
            current_root,
            symbol.path,
            "regression_attribution",
            [(line, line) for line in sorted(lines[symbol])],
            f"{symbol.qualified_name} changed and is executed by failing regression(s) "
            + ", ".join(sorted(nodes[symbol])),
        )
