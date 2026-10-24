"""Production-only architecture (H) evidence producer.

Ported from ``slop-code-bench`` ``src/slop_code/metrics/scoring/production_graph.py``
at ``25069ba``. The call graph's nodes are joined to the assessor inventory;
only production-to-production edges are scored, cross-role edges are kept as
evidence, and ``H = 1 - cyclic mass / total mass`` where cyclic mass is the
weight of edges inside a strongly connected component of two or more
functions (or a self-loop). An empty production graph scores 1.

The fork builds the SCCs with networkx on the host; here a deterministic
iterative Tarjan computes the same partition (every SCC is re-sorted, so the
enumeration order never reaches the evidence), keeping networkx inside the
pinned tool environment.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, TypeGuard

from raidar.scoring.code_quality.models import FileRole, ProductionGraphEvidence

_ROLES = frozenset(role.value for role in FileRole)
_PRODUCTION = FileRole.PRODUCTION.value


class ProductionGraphError(ValueError):
    """Evidence cannot be used to compute the production architecture component."""


@dataclass(frozen=True)
class NodeSpan:
    path: str
    line: int
    end_line: int


@dataclass(frozen=True)
class ProductionGraphResult:
    """Raw, independently recomputable production architecture evidence."""

    architecture: Decimal
    evidence: ProductionGraphEvidence
    spans: Mapping[str, NodeSpan]


def _is_string_mapping(row: object) -> TypeGuard[Mapping[str, object]]:
    return isinstance(row, Mapping) and all(isinstance(key, str) for key in row)


def _value(row: object, *names: str) -> object:
    mapping: Mapping[str, object] | None = None
    if isinstance(row, Mapping):
        if not _is_string_mapping(row):
            raise ProductionGraphError(
                "score_evidence_invalid: inventory mapping keys must be strings"
            )
        mapping = row
    for name in names:
        if mapping is not None and name in mapping:
            return mapping[name]
        if hasattr(row, name):
            return getattr(row, name)
    return None


def _role(value: object) -> str:
    if isinstance(value, FileRole | str) and str(value) in _ROLES:
        return str(value)
    raise ProductionGraphError("score_evidence_invalid: invalid inventory role")


def _path(row: object) -> str:
    path = _value(row, "path", "file_path")
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
        raise ProductionGraphError("score_evidence_invalid: invalid lexical file identity")
    return path


def _inventory_roles(inventory: Iterable[object]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for item in inventory:
        path = _path(item)
        if path in roles:
            raise ProductionGraphError("score_evidence_invalid: duplicate inventory identity")
        roles[path] = _role(_value(item, "role"))
    return roles


def _weight(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise ProductionGraphError("score_evidence_invalid: malformed graph edge weight")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ProductionGraphError("score_evidence_invalid: malformed graph edge weight") from error
    if not result.is_finite() or result < 0:
        raise ProductionGraphError("score_evidence_invalid: invalid graph edge weight")
    return result


def _utf8(value: str) -> bytes:
    return value.encode("utf-8")


class _Tarjan:
    """Iterative Tarjan over a successor map (no recursion limit on deep call chains)."""

    def __init__(self, edges: Mapping[str, Sequence[str]]) -> None:
        self.edges = edges
        self.index: dict[str, int] = {}
        self.low: dict[str, int] = {}
        self.stack: list[str] = []
        self.on_stack: set[str] = set()
        self.components: list[tuple[str, ...]] = []

    def visit(self, root: str) -> None:
        work = [(root, 0)]
        while work:
            node, position = work.pop()
            if position == 0:
                self.index[node] = self.low[node] = len(self.index)
                self.stack.append(node)
                self.on_stack.add(node)
            successors = self.edges.get(node, ())
            if position < len(successors):
                work.append((node, position + 1))
                self._follow(node, successors[position], work)
                continue
            if work:
                parent = work[-1][0]
                self.low[parent] = min(self.low[parent], self.low[node])
            if self.low[node] == self.index[node]:
                self._close(node)

    def _follow(self, node: str, target: str, work: list[tuple[str, int]]) -> None:
        if target not in self.index:
            work.append((target, 0))
        elif target in self.on_stack:
            self.low[node] = min(self.low[node], self.index[target])

    def _close(self, node: str) -> None:
        members = []
        while True:
            member = self.stack.pop()
            self.on_stack.discard(member)
            members.append(member)
            if member == node:
                break
        self.components.append(tuple(sorted(members, key=_utf8)))


def strongly_connected_components(
    nodes: Sequence[str], edges: Mapping[str, Sequence[str]]
) -> list[tuple[str, ...]]:
    """Tarjan's SCCs; each component sorted in UTF-8 byte order."""
    tarjan = _Tarjan(edges)
    for root in nodes:
        if root not in tarjan.index:
            tarjan.visit(root)
    return tarjan.components


@dataclass
class _Edges:
    production: list[dict[str, Any]]
    cross_role: list[dict[str, Any]]


def _classified_edges(
    traversal_edges: Iterable[Mapping[str, Any]],
    node_paths: Mapping[str, str],
    roles: Mapping[str, str],
) -> _Edges:
    edges = _Edges([], [])
    for edge in traversal_edges:
        source, target = edge.get("source"), edge.get("target")
        source_path = node_paths.get(source) if isinstance(source, str) else None
        target_path = node_paths.get(target) if isinstance(target, str) else None
        if source_path is None or target_path is None:
            raise ProductionGraphError("score_evidence_invalid: graph edge reconciliation failed")
        record = {
            "source": source,
            "target": target,
            "weight": _weight(edge.get("weight", 1)),
            "source_role": roles[source_path],
            "target_role": roles[target_path],
            "cyclic": False,
            "self_loop": source == target,
        }
        if roles[source_path] == roles[target_path] == _PRODUCTION:
            edges.production.append(record)
        elif roles[source_path] != roles[target_path]:
            edges.cross_role.append(record)
    return edges


def _node_paths(
    nodes: Iterable[Mapping[str, Any]], roles: Mapping[str, str]
) -> tuple[dict[str, str], dict[str, NodeSpan]]:
    paths: dict[str, str] = {}
    spans: dict[str, NodeSpan] = {}
    for node in nodes:
        identity, path = node.get("identity"), node.get("path")
        if (
            not isinstance(identity, str)
            or "::" not in identity
            or not isinstance(path, str)
            or not path
            or path not in roles
        ):
            raise ProductionGraphError("score_evidence_invalid: graph node reconciliation failed")
        paths[identity] = path
        line, end_line = node.get("line"), node.get("end_line")
        if isinstance(line, int) and isinstance(end_line, int):
            spans[identity] = NodeSpan(path=path, line=line, end_line=end_line)
    return paths, spans


@dataclass(frozen=True)
class _Components:
    members: list[tuple[str, ...]]
    membership: dict[str, int]

    def same(self, edge: Mapping[str, Any]) -> bool:
        return self.membership[edge["source"]] == self.membership[edge["target"]]

    def cyclic(self, edge: Mapping[str, Any]) -> bool:
        size = len(self.members[self.membership[edge["source"]]])
        return self.same(edge) and (size > 1 or bool(edge["self_loop"]))


def _components(production_ids: list[str], edges: list[dict[str, Any]]) -> _Components:
    successors: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        successors[edge["source"]].append(edge["target"])
    members = strongly_connected_components(production_ids, successors)
    membership = {member: number for number, group in enumerate(members) for member in group}
    return _Components(members, membership)


def _scc_evidence(components: _Components, edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mass = [Decimal(0) for _ in components.members]
    for edge in edges:
        if components.same(edge):
            mass[components.membership[edge["source"]]] += edge["weight"]
    self_loops = {edge["source"] for edge in edges if edge["self_loop"]}
    sccs = []
    for number, members in enumerate(components.members):
        self_loop = len(members) == 1 and members[0] in self_loops
        sccs.append(
            {
                "members": members,
                "mass": str(mass[number]),
                "cyclic": len(members) > 1 or self_loop,
                "self_loop": self_loop,
            }
        )
    return sorted(sccs, key=lambda item: tuple(_utf8(member) for member in item["members"]))


def _edge_key(edge: Mapping[str, Any]) -> tuple[bytes, bytes]:
    return _utf8(edge["source"]), _utf8(edge["target"])


def _edge_rows(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**edge, "weight": str(edge["weight"])} for edge in sorted(edges, key=_edge_key)]


def _architecture(edges: list[dict[str, Any]]) -> tuple[Decimal, Decimal, Decimal]:
    total_mass = sum((edge["weight"] for edge in edges), Decimal(0))
    cyclic_mass = sum((edge["weight"] for edge in edges if edge["cyclic"]), Decimal(0))
    with localcontext() as context:
        context.prec = 50
        if total_mass == 0:
            return Decimal(1), cyclic_mass, total_mass
        return Decimal(1) - cyclic_mass / total_mass, cyclic_mass, total_mass


def produce_production_graph(
    checkpoint_id: str, inventory: Iterable[object], traversal: Mapping[str, Any]
) -> ProductionGraphResult:
    """Join inventory roles to the snapshot's call-graph traversal and compute H."""
    if not checkpoint_id:
        raise ProductionGraphError("score_evidence_invalid: checkpoint identity missing")
    roles = _inventory_roles(inventory)
    node_paths, spans = _node_paths(traversal.get("nodes", ()), roles)
    production_nodes = [
        {"identity": identity, "path": path}
        for identity, path in node_paths.items()
        if roles[path] == _PRODUCTION
    ]
    edges = _classified_edges(traversal.get("edges", ()), node_paths, roles)
    components = _components([node["identity"] for node in production_nodes], edges.production)
    for edge in edges.production:
        edge["cyclic"] = components.cyclic(edge)
    architecture, cyclic_mass, total_mass = _architecture(edges.production)
    evidence = ProductionGraphEvidence.model_validate(
        {
            "checkpoint_id": checkpoint_id,
            "nodes": production_nodes,
            "edges": _edge_rows(edges.production),
            "cross_role_edges": _edge_rows(edges.cross_role),
            "sccs": _scc_evidence(components, edges.production),
            "cyclic_mass": str(cyclic_mass),
            "total_mass": str(total_mass),
            "architecture": str(architecture),
        }
    )
    return ProductionGraphResult(architecture=architecture, evidence=evidence, spans=spans)
