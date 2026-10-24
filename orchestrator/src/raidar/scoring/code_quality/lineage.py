"""Pure, deterministic repeated-rework (R) lineage kernel and ledger.

Ported from ``slop-code-bench`` ``src/slop_code/metrics/scoring/lineage.py``
at ``25069ba``. The algorithm is unchanged (``LINEAGE_CONTRACT`` below):

- exact identity (language, kind, parent, name, path) accepts a unique pair;
- the remaining symbols form a complete bipartite candidate graph over any
  equal non-empty body/structure/signature hash; a component edge is accepted
  only when body and structure are equal and both endpoints have degree one,
  otherwise the component is ambiguous;
- each changed physical line is owned by the innermost symbol once;
- R = 1 - L_repeat / L_changed (1 when nothing changed), where a line repeats
  when its lineage was already modified in an earlier transition (the ledger).

The fork's single long function is split into helpers so each stays small;
iteration orders, sorting keys and Decimal arithmetic are the fork's.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal

from raidar.scoring.code_quality.lineage_models import (
    LedgerTransitionEvidence,
    LineageCandidate,
    LineageComponentEvidence,
    LineageLedgerEntry,
    OwnedLineEvidence,
    PhysicalLineEvidence,
    ProblemReworkEvidence,
    ReworkCheckpointInput,
    StableIdentity,
    SymbolDegreeEvidence,
    SymbolIdentity,
    TransitionReworkEvidence,
)

Ledger = Mapping[object, bool]
LineKey = tuple[str, str, int]
_Node = tuple[str, SymbolIdentity]

LINEAGE_CONTRACT = {
    "exact_identity": (
        "language",
        "kind",
        "parent_qualified_name",
        "qualified_name",
        "project_relative_path",
    ),
    "candidate_edge": "same_language_and_kind_and_any_nonempty_equal_sha256",
    "hash_acceptance": "body_and_structure_equal_and_endpoint_degrees_one",
    "ambiguous_component_id": "sha256(sorted_prior_current_identities_and_flags)",
    "line_owner": "innermost_production_symbol_once_per_physical_diff_line",
    "rework": "1-L_repeat/L_changed;one_when_denominator_zero",
    "ledger": "accepted:prior_or_mapped;ambiguous:prior_union_or_set_union_mapped",
    "gap": "reset_at_problem_boundary_and_missing_checkpoint",
}


def _endpoint_key(symbol: SymbolIdentity) -> StableIdentity:
    return symbol.stable_identity


def _ordered_symbols(symbols: Sequence[SymbolIdentity]) -> tuple[SymbolIdentity, ...]:
    return tuple(sorted(symbols, key=_endpoint_key))


def _equal(left: str, right: str) -> bool:
    return bool(left) and left == right


def _candidate(
    prior: SymbolIdentity,
    current: SymbolIdentity,
    *,
    accepted: bool = False,
    match: str = "none",
    ambiguous_set_id: str | None = None,
) -> LineageCandidate:
    return LineageCandidate(
        prior=prior,
        current=current,
        body_hash_equal=_equal(prior.body_sha256, current.body_sha256),
        structure_hash_equal=_equal(prior.structure_sha256, current.structure_sha256),
        signature_hash_equal=_equal(prior.signature_sha256, current.signature_sha256),
        accepted=accepted,
        match=match,  # type: ignore[arg-type]
        ambiguous_set_id=ambiguous_set_id,
    )


def _pair_key(edge: LineageCandidate) -> tuple[StableIdentity, StableIdentity]:
    return _endpoint_key(edge.prior), _endpoint_key(edge.current)


def _component_id(
    priors: Sequence[SymbolIdentity],
    currents: Sequence[SymbolIdentity],
    edges: Sequence[LineageCandidate],
) -> str:
    """Hash sorted endpoint identities and every frozen candidate equality flag."""
    payload = repr(
        (
            tuple(_endpoint_key(symbol) for symbol in _ordered_symbols(priors)),
            tuple(_endpoint_key(symbol) for symbol in _ordered_symbols(currents)),
            tuple(
                (
                    _endpoint_key(edge.prior),
                    _endpoint_key(edge.current),
                    edge.body_hash_equal,
                    edge.structure_hash_equal,
                    edge.signature_hash_equal,
                )
                for edge in sorted(edges, key=_pair_key)
            ),
        )
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _ledger_value(ledger: Ledger, symbol: SymbolIdentity) -> bool:
    return bool(ledger.get(symbol, ledger.get(_endpoint_key(symbol), False)))


def _innermost(symbols: Sequence[SymbolIdentity], path: str, line: int) -> SymbolIdentity | None:
    candidates = [
        symbol
        for symbol in symbols
        if symbol.path == path and symbol.start_line <= line <= symbol.end_line
    ]
    return min(
        candidates,
        key=lambda symbol: (symbol.end_line - symbol.start_line, _endpoint_key(symbol)),
        default=None,
    )


def _exact_matches(
    priors: tuple[SymbolIdentity, ...], currents: tuple[SymbolIdentity, ...]
) -> tuple[list[LineageCandidate], set[SymbolIdentity], set[SymbolIdentity]]:
    prior_by_exact: dict[tuple[str, ...], list[SymbolIdentity]] = defaultdict(list)
    current_by_exact: dict[tuple[str, ...], list[SymbolIdentity]] = defaultdict(list)
    for symbol in priors:
        prior_by_exact[symbol.exact_identity].append(symbol)
    for symbol in currents:
        current_by_exact[symbol.exact_identity].append(symbol)
    accepted: list[LineageCandidate] = []
    matched_priors: set[SymbolIdentity] = set()
    matched_currents: set[SymbolIdentity] = set()
    for identity in sorted(set(prior_by_exact) & set(current_by_exact)):
        left, right = prior_by_exact[identity], current_by_exact[identity]
        if len(left) == len(right) == 1:
            accepted.append(_candidate(left[0], right[0], accepted=True, match="exact"))
            matched_priors.add(left[0])
            matched_currents.add(right[0])
    return accepted, matched_priors, matched_currents


def _hash_edge(prior: SymbolIdentity, current: SymbolIdentity) -> bool:
    return (
        prior.language == current.language
        and prior.kind == current.kind
        and any(
            (
                _equal(prior.body_sha256, current.body_sha256),
                _equal(prior.structure_sha256, current.structure_sha256),
                _equal(prior.signature_sha256, current.signature_sha256),
            )
        )
    )


def _connected(
    seed: _Node, adjacency: Mapping[_Node, set[_Node]], visited: set[_Node]
) -> set[_Node]:
    stack, nodes = [seed], set()
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        nodes.add(node)
        stack.extend(adjacency[node] - visited)
    return nodes


class _Partition:
    """Components of the complete candidate graph and their classified edges."""

    def __init__(self, raw_edges: list[LineageCandidate]) -> None:
        self.raw_edges = raw_edges
        self.inbound: dict[SymbolIdentity, int] = defaultdict(int)
        self.outbound: dict[SymbolIdentity, int] = defaultdict(int)
        for edge in raw_edges:
            self.outbound[edge.prior] += 1
            self.inbound[edge.current] += 1
        self.components: list[LineageComponentEvidence] = []
        self.candidates: list[LineageCandidate] = []

    def run(self) -> None:
        # Partition the complete candidate graph, never a subset selected by a match.
        adjacency: dict[_Node, set[_Node]] = defaultdict(set)
        for edge in self.raw_edges:
            left, right = ("prior", edge.prior), ("current", edge.current)
            adjacency[left].add(right)
            adjacency[right].add(left)
        visited: set[_Node] = set()
        for seed in sorted(adjacency, key=lambda item: (item[0], _endpoint_key(item[1]))):
            if seed not in visited:
                self._component(_connected(seed, adjacency, visited))

    def _degree_one(self, edge: LineageCandidate) -> bool:
        return self.outbound[edge.prior] == 1 and self.inbound[edge.current] == 1

    def _component(self, nodes: set[_Node]) -> None:
        priors, currents = _sides(nodes)
        edges = [edge for edge in self.raw_edges if _within(edge, priors, currents)]
        component_id = _component_id(priors, currents, edges)
        hash_accepts = [edge for edge in edges if _content_equal(edge) and self._degree_one(edge)]
        self.components.append(
            LineageComponentEvidence(
                component_id=component_id,
                priors=priors,
                currents=currents,
                ambiguous=not bool(hash_accepts),
            )
        )
        self.candidates.extend(_classified(edge, hash_accepts, component_id) for edge in edges)


def _sides(nodes: set[_Node]) -> tuple[tuple[SymbolIdentity, ...], tuple[SymbolIdentity, ...]]:
    priors = _ordered_symbols([symbol for side, symbol in nodes if side == "prior"])
    currents = _ordered_symbols([symbol for side, symbol in nodes if side == "current"])
    return priors, currents


def _classified(
    edge: LineageCandidate, hash_accepts: list[LineageCandidate], component_id: str
) -> LineageCandidate:
    if edge in hash_accepts:
        return _candidate(edge.prior, edge.current, accepted=True, match="hash")
    return _candidate(edge.prior, edge.current, ambiguous_set_id=component_id)


def _within(
    edge: LineageCandidate,
    priors: tuple[SymbolIdentity, ...],
    currents: tuple[SymbolIdentity, ...],
) -> bool:
    return edge.prior in priors and edge.current in currents


def _content_equal(edge: LineageCandidate) -> bool:
    return edge.body_hash_equal and edge.structure_hash_equal


def _map_lines(
    lines: Sequence[PhysicalLineEvidence],
    priors: tuple[SymbolIdentity, ...],
    currents: tuple[SymbolIdentity, ...],
) -> tuple[PhysicalLineEvidence, ...]:
    return tuple(
        PhysicalLineEvidence(
            path=line.path,
            line=line.line,
            side=line.side,
            owner=_innermost(priors if line.side == "removed" else currents, line.path, line.line),
        )
        for line in lines
    )


def _eligible_symbol_keys(candidates: Sequence[LineageCandidate]) -> set[StableIdentity]:
    accepted_pairs = {(edge.prior, edge.current) for edge in candidates if edge.accepted}
    ambiguous_ids = {edge.ambiguous_set_id for edge in candidates if edge.ambiguous_set_id}
    return {_endpoint_key(symbol) for pair in accepted_pairs for symbol in pair} | {
        _endpoint_key(symbol)
        for edge in candidates
        if edge.ambiguous_set_id in ambiguous_ids
        for symbol in (edge.prior, edge.current)
    }


def _ownership(
    mapped: Sequence[PhysicalLineEvidence], candidates: Sequence[LineageCandidate]
) -> tuple[dict[SymbolIdentity, set[LineKey]], set[LineKey], tuple[OwnedLineEvidence, ...]]:
    eligible = _eligible_symbol_keys(candidates)
    owned_map: dict[SymbolIdentity, set[int]] = defaultdict(set)
    changed_keys: set[LineKey] = set()
    by_symbol: dict[SymbolIdentity, set[LineKey]] = defaultdict(set)
    for line in mapped:
        if line.owner is not None and _endpoint_key(line.owner) in eligible:
            key = (line.side, line.path, line.line)
            changed_keys.add(key)
            by_symbol[line.owner].add(key)
            owned_map[line.owner].add(line.line)
    owned = tuple(
        OwnedLineEvidence(symbol=symbol, lines=tuple(sorted(lines)))
        for symbol, lines in sorted(owned_map.items(), key=lambda item: _endpoint_key(item[0]))
    )
    return by_symbol, changed_keys, owned


def _transition(
    prior: SymbolIdentity, current: SymbolIdentity, before: bool, changed: bool
) -> LedgerTransitionEvidence:
    return LedgerTransitionEvidence(
        prior=prior,
        current=current,
        input_modified_before=before,
        mapped_changed=changed,
        output_modified_before=before or changed,
    )


def _edge_lines(
    by_symbol: Mapping[SymbolIdentity, set[LineKey]], edge: LineageCandidate
) -> set[LineKey]:
    return by_symbol.get(edge.prior, set()) | by_symbol.get(edge.current, set())


def _ambiguous_transitions(
    component: LineageComponentEvidence,
    edges: Sequence[LineageCandidate],
    by_symbol: Mapping[SymbolIdentity, set[LineKey]],
    prior_ledger: Ledger,
    repeated: set[LineKey],
) -> list[LedgerTransitionEvidence]:
    line_keys: set[LineKey] = set().union(*(_edge_lines(by_symbol, edge) for edge in edges))
    before = any(_ledger_value(prior_ledger, edge.prior) for edge in edges)
    if before:
        repeated.update(line_keys)
    return [
        _transition(component.priors[0], current, before, bool(line_keys))
        for current in component.currents
    ]


def _ledger_transitions(
    candidates: Sequence[LineageCandidate],
    components: Sequence[LineageComponentEvidence],
    by_symbol: Mapping[SymbolIdentity, set[LineKey]],
    prior_ledger: Ledger,
) -> tuple[set[LineKey], list[LedgerTransitionEvidence]]:
    repeated: set[LineKey] = set()
    transitions: list[LedgerTransitionEvidence] = []
    for edge in (edge for edge in candidates if edge.accepted):
        line_keys = _edge_lines(by_symbol, edge)
        before = _ledger_value(prior_ledger, edge.prior)
        if before:
            repeated.update(line_keys)
        transitions.append(_transition(edge.prior, edge.current, before, bool(line_keys)))
    for component in components:
        edges = [edge for edge in candidates if edge.ambiguous_set_id == component.component_id]
        if edges:
            transitions.extend(
                _ambiguous_transitions(component, edges, by_symbol, prior_ledger, repeated)
            )
    return repeated, transitions


def _ledger_reason(edge: LineageCandidate) -> str:
    if edge.match == "exact":
        return "exact identity"
    if edge.match == "hash":
        return "body and structure degree-one"
    return "ambiguous candidate set"


def _ledger(
    candidates: Sequence[LineageCandidate],
    transitions: Sequence[LedgerTransitionEvidence],
    prior_ledger: Ledger,
) -> tuple[LineageLedgerEntry, ...]:
    return tuple(
        LineageLedgerEntry(
            candidate=edge,
            disposition="accepted" if edge.accepted else "rejected",
            reason=_ledger_reason(edge),
            prior_modified_before=_ledger_value(prior_ledger, edge.prior),
            current_modified_before=next(
                (
                    transition.output_modified_before
                    for transition in transitions
                    if transition.current == edge.current
                ),
                False,
            ),
        )
        for edge in candidates
    )


def _partition(
    priors: tuple[SymbolIdentity, ...], currents: tuple[SymbolIdentity, ...]
) -> tuple[list[LineageCandidate], _Partition]:
    accepted, matched_priors, matched_currents = _exact_matches(priors, currents)
    remaining_currents = tuple(symbol for symbol in currents if symbol not in matched_currents)
    partition = _Partition(
        [
            _candidate(prior, current)
            for prior in priors
            if prior not in matched_priors
            for current in remaining_currents
            if _hash_edge(prior, current)
        ]
    )
    partition.run()
    return sorted([*accepted, *partition.candidates], key=_pair_key), partition


def _rework(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("1")
    return Decimal("1") - Decimal(numerator) / Decimal(denominator)


def _check_sides(
    removed_lines: Sequence[PhysicalLineEvidence], added_lines: Sequence[PhysicalLineEvidence]
) -> None:
    if any(line.side != "removed" for line in removed_lines) or any(
        line.side != "added" for line in added_lines
    ):
        raise ValueError("diff line side must match its transition input")


def calculate_transition_rework(
    prior_symbols: Sequence[SymbolIdentity],
    current_symbols: Sequence[SymbolIdentity],
    removed_lines: Sequence[PhysicalLineEvidence],
    added_lines: Sequence[PhysicalLineEvidence],
    prior_ledger: Ledger,
) -> TransitionReworkEvidence:
    """Score one transition using only frozen exact/hash lineage evidence.

    ``removed_lines`` must contain only ``side='removed'`` entries and
    ``added_lines`` only ``side='added'`` entries.  Every supplied line is
    mapped once before candidate ownership is considered.
    """
    _check_sides(removed_lines, added_lines)
    priors = _ordered_symbols(prior_symbols)
    currents = _ordered_symbols(current_symbols)
    candidates, partition = _partition(priors, currents)
    degrees = tuple(
        SymbolDegreeEvidence(
            symbol=symbol,
            inbound=partition.inbound.get(symbol, 0),
            outbound=partition.outbound.get(symbol, 0),
        )
        for symbol in _ordered_symbols((*priors, *currents))
    )
    mapped = _map_lines((*removed_lines, *added_lines), priors, currents)
    by_symbol, changed_keys, owned = _ownership(mapped, candidates)
    repeated, transitions = _ledger_transitions(
        candidates, partition.components, by_symbol, prior_ledger
    )
    return TransitionReworkEvidence(
        changed_physical_lines=mapped,
        lineage_candidates=tuple(candidates),
        symbol_degrees=degrees,
        lineage_components=tuple(partition.components),
        owned_lines=owned,
        lineage_ledger=_ledger(candidates, transitions, prior_ledger),
        ledger_transitions=tuple(transitions),
        changed_lines=len(changed_keys),
        repeated_lines=len(repeated),
        rework=_rework(len(repeated), len(changed_keys)),
    )


def next_ledger(transition: TransitionReworkEvidence) -> dict[object, bool]:
    """The modified-before ledger a transition hands to the next one."""
    return {
        item.current.stable_identity: item.output_modified_before
        for item in transition.ledger_transitions
    }


def calculate_problem_rework(
    checkpoints: Sequence[ReworkCheckpointInput],
) -> ProblemReworkEvidence:
    """Calculate every configured adjacent transition from complete evidence."""
    if len(checkpoints) < 2:
        raise ValueError("rework requires at least two configured checkpoints")
    checkpoint_ids = tuple(checkpoint.checkpoint_id for checkpoint in checkpoints)
    if len(set(checkpoint_ids)) != len(checkpoint_ids):
        raise ValueError("checkpoint identifiers must be unique")
    ledger: dict[object, bool] = {}
    transitions: list[TransitionReworkEvidence] = []
    prior = checkpoints[0]
    for current in checkpoints[1:]:
        if prior.symbols is None or current.symbols is None:
            raise ValueError("rework requires complete symbol evidence")
        transition = calculate_transition_rework(
            prior.symbols, current.symbols, current.removed_lines, current.added_lines, ledger
        )
        transitions.append(transition)
        ledger = next_ledger(transition)
        prior = current
    final_ledger = tuple(
        LedgerTransitionEvidence(
            prior=item.prior,
            current=item.current,
            input_modified_before=item.output_modified_before,
            mapped_changed=False,
            output_modified_before=item.output_modified_before,
        )
        for item in transitions[-1].ledger_transitions
    )
    return ProblemReworkEvidence(
        checkpoint_ids=checkpoint_ids, transitions=tuple(transitions), final_ledger=final_ledger
    )
