"""Adjacent-snapshot diffs and the symbol scopes they feed to R and G.

Ported from ``slop-code-bench`` ``src/slop_code/metrics/scoring/live_evidence.py``
(``_changed_lines``) and ``live_regression.py`` (``_current_production_symbols``,
``_changed_production_symbols``, ``_accepted_changed_current_symbols``) at
``25069ba``; the fork's run-directory plumbing is replaced by the stored
per-step snapshots.

Both symbol projections follow the fork's live producers exactly, so R and G
are ``scb-exact`` against the fork's live pipeline:

- R (``rework_symbols``) uses the fork's ``live_evidence._symbols_from_raw``:
  V/E's eligible-symbol identities, which carry no hashes and no parent class.
  The lineage kernel's hash matching (renames and moves) therefore never fires
  in the live pipeline, and every exact match counts as "changed" for G's
  accepted-lineage scope; that is the fork's behaviour and is kept.
- G's diff owners (``production_symbols``) use the fork's
  ``live_regression._current_production_symbols``: the hashed tree-sitter rows.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from raidar.scoring.code_quality.lineage_models import (
    LineageCandidate,
    PhysicalLineEvidence,
    StableIdentity,
    SymbolIdentity,
)
from raidar.scoring.code_quality.models import ProductionQualityRawEvidence

_ELIGIBLE_KINDS = frozenset({"function", "method"})


def _utf8(value: str) -> bytes:
    return value.encode()


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


def changed_lines(
    prior_snapshot: Path, current_snapshot: Path, paths: Iterable[str]
) -> tuple[tuple[PhysicalLineEvidence, ...], tuple[PhysicalLineEvidence, ...]]:
    """Deduplicated removed and added physical lines between adjacent snapshots."""
    removed: set[tuple[str, int]] = set()
    added: set[tuple[str, int]] = set()
    for path in sorted(set(paths), key=_utf8):
        matcher = difflib.SequenceMatcher(
            None, _lines(prior_snapshot / path), _lines(current_snapshot / path), autojunk=False
        )
        for tag, prior_start, prior_end, current_start, current_end in matcher.get_opcodes():
            if tag != "equal":
                removed.update((path, line) for line in range(prior_start + 1, prior_end + 1))
                added.update((path, line) for line in range(current_start + 1, current_end + 1))

    def ordered(
        lines: set[tuple[str, int]], side: Literal["added", "removed"]
    ) -> tuple[PhysicalLineEvidence, ...]:
        return tuple(
            PhysicalLineEvidence(path=path, line=line, side=side)
            for path, line in sorted(lines, key=lambda item: (_utf8(item[0]), item[1]))
        )

    return ordered(removed, "removed"), ordered(added, "added")


def rework_symbols(evidence: ProductionQualityRawEvidence) -> tuple[SymbolIdentity, ...]:
    """R's lineage identities: V/E's eligible symbols, unhashed (fork ``_symbols_from_raw``)."""
    return tuple(
        SymbolIdentity(
            path=path,
            qualified_name=qualified_name,
            kind=kind,
            start_line=start_line,
            end_line=end_line,
        )
        for path, qualified_name, kind, start_line, end_line in (
            item.identity for item in evidence.eligible_symbols
        )
    )


def production_symbols(
    symbol_rows: Iterable[Mapping[str, Any]], production_paths: Iterable[str]
) -> tuple[SymbolIdentity, ...]:
    """G's diff owners: hashed production functions and methods, deduplicated."""
    paths = set(production_paths)
    identities: dict[StableIdentity, SymbolIdentity] = {}
    for row in symbol_rows:
        if row["file_path"] not in paths or row["type"] not in _ELIGIBLE_KINDS:
            continue
        symbol = SymbolIdentity(
            path=row["file_path"],
            qualified_name=row["name"],
            kind=row["type"],
            start_line=row["start"],
            end_line=row["end"],
            parent_qualified_name=row.get("parent_class") or "",
            body_sha256=row.get("body_hash") or "",
            structure_sha256=row.get("structure_hash") or "",
            signature_sha256=row.get("signature_hash") or "",
        )
        identities[symbol.stable_identity] = symbol
    return tuple(sorted(identities.values(), key=lambda item: item.stable_identity))


def changed_production_symbols(
    added: Sequence[PhysicalLineEvidence], symbols: Sequence[SymbolIdentity]
) -> tuple[SymbolIdentity, ...]:
    """Current symbols owning an added line (innermost by the fork's G ordering)."""
    changed: dict[StableIdentity, SymbolIdentity] = {}
    for line in added:
        matches = [
            symbol
            for symbol in symbols
            if symbol.path == line.path and symbol.start_line <= line.line <= symbol.end_line
        ]
        if matches:
            symbol = max(
                matches,
                key=lambda candidate: (
                    candidate.start_line,
                    -candidate.end_line,
                    candidate.stable_identity,
                ),
            )
            changed[symbol.stable_identity] = symbol
    return tuple(sorted(changed.values(), key=lambda item: item.stable_identity))


def accepted_changed_current_symbols(
    candidates: Iterable[LineageCandidate],
) -> tuple[SymbolIdentity, ...]:
    """Current endpoints of accepted lineages that are not exact, fully hash-equal matches."""
    changed: dict[StableIdentity, SymbolIdentity] = {}
    for candidate in candidates:
        if not candidate.accepted:
            continue
        if (
            candidate.prior.exact_identity == candidate.current.exact_identity
            and candidate.body_hash_equal
            and candidate.structure_hash_equal
            and candidate.signature_hash_equal
        ):
            continue
        changed[candidate.current.stable_identity] = candidate.current
    return tuple(sorted(changed.values(), key=lambda item: item.stable_identity))


def regression_scope(
    added: Sequence[PhysicalLineEvidence],
    current_symbols: Sequence[SymbolIdentity],
    candidates: Iterable[LineageCandidate],
) -> tuple[SymbolIdentity, ...]:
    """G's changed-symbol scope: diff owners united with changed accepted lineages."""
    merged = {
        symbol.stable_identity: symbol
        for symbol in (
            *changed_production_symbols(added, current_symbols),
            *accepted_changed_current_symbols(candidates),
        )
    }
    return tuple(sorted(merged.values(), key=lambda item: item.stable_identity))
