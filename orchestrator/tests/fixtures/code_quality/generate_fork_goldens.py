"""Regenerate ``fork_goldens.json`` by running the SCB fork's own scoring code.

Run with the fork's interpreter (it needs ``slop_code`` importable and Ruff
0.8.6 under CPython 3.12), from the orchestrator directory::

    <slop-code-bench>/.venv/bin/python tests/fixtures/code_quality/generate_fork_goldens.py \\
        --fork <slop-code-bench> --forge <scb-problems>/forge/solutions \\
        --forge-runs <trial dir with raidar-oracle/steps/*/g-kernel-input.json>

The output holds every input (snapshot trees, run evidence, kernel scenarios,
formula cases) beside the fork's results, so the Raidar golden tests replay
the same inputs through the ported code and compare. Nothing here is imported
by Raidar or collected by pytest.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "fork_goldens.json.gz"
SYNTHETIC_ENTRY = "app.py"


def _h(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


# --------------------------------------------------------------------------- synthetic snapshots
_APP = '''"""Entry point."""

import sys

from shop import cart
from shop.pricing import total


def main(argv):
    items = cart.load(argv)
    print(total(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''

_CART_V1 = '''"""Cart loading."""


def load(argv):
    items = []
    for raw in argv:
        items.append(parse(raw))
    return items


def parse(raw):
    if ":" in raw:
        name = raw.split(":")[0]
    else:
        name = raw
    return {"name": name, "qty": count(raw)}


def count(raw):
    parts = raw.split(":")
    qty = 1
    if len(parts) > 1:
        qty = int(parts[1])
    if qty < 0:
        qty = 0
    return qty


def summarize(items):
    names = []
    total_qty = 0
    for item in items:
        names.append(item["name"])
    total_qty = sum(item["qty"] for item in items)
    label = ", ".join(names)
    width = len(label)
    return label, total_qty, width


def describe(items):
    names = []
    total_qty = 0
    for item in items:
        names.append(item["name"])
    total_qty = sum(item["qty"] for item in items)
    label = ", ".join(names)
    width = len(label)
    return label.upper(), total_qty, width
'''

_PRICING_V1 = '''"""Pricing with a deliberate mutual recursion and a branchy rule table."""

from shop import cart


def total(items):
    subtotal = 0
    for item in items:
        subtotal += price(item)
    return apply(subtotal, len(items))


def apply(subtotal, count):
    if count > 10:
        return total([]) + subtotal * 0.9
    return subtotal


def price(item):
    name = item["name"]
    qty = cart.count(name)
    if name == "a":
        base = 1
    elif name == "b":
        base = 2
    elif name == "c":
        base = 3
    elif name == "d":
        base = 4
    elif name == "e":
        base = 5
    elif name == "f":
        base = 6
    elif name == "g":
        base = 7
    elif name == "h":
        base = 8
    elif name == "i":
        base = 9
    elif name == "j":
        base = 10
    else:
        base = 0
    if qty and base and name:
        return base * qty
    return 0
'''

_TEST = """from shop import cart


def test_load():
    assert cart.load(["a:2"]) == [{"name": "a", "qty": 2}]
"""

_CART_V2 = '''"""Cart loading."""

from shop.util import clamp


def load(argv):
    items = []
    for raw in argv:
        items.append(parse_item(raw))
    return items


def parse_item(raw):
    if ":" in raw:
        name = raw.split(":")[0]
    else:
        name = raw
    return {"name": name, "qty": count(raw)}


def count(raw):
    parts = raw.split(":")
    qty = 1
    if len(parts) > 1:
        qty = int(parts[1].strip())
    return clamp(qty)


def summarize(items):
    names = []
    total_qty = 0
    for item in items:
        names.append(item["name"])
    total_qty = sum(item["qty"] for item in items)
    label = ", ".join(names)
    width = len(label)
    return label, total_qty, width
'''

_UTIL_V2 = '''"""Shared helpers."""


def clamp(value):
    return value if value > 0 else 0


def describe(items):
    names = []
    total_qty = 0
    for item in items:
        names.append(item["name"])
    total_qty = sum(item["qty"] for item in items)
    label = ", ".join(names)
    width = len(label)
    return label.upper(), total_qty, width
'''

_CART_V3 = '''"""Cart loading."""

from shop.util import clamp


class Loader:
    def __init__(self, argv):
        self.argv = argv

    def load(self):
        return [self.parse_item(raw) for raw in self.argv]

    def parse_item(self, raw):
        name = raw.split(":")[0] if ":" in raw else raw
        return {"name": name, "qty": count(raw)}


class StrictLoader(Loader):
    def load(self):
        items = super().load()
        return [item for item in items if item["qty"]]


def load(argv):
    return StrictLoader(argv).load()


def count(raw):
    parts = raw.split(":")
    qty = 1
    if len(parts) > 1:
        qty = int(parts[1].strip() or "0")
    return clamp(qty)


def summarize(items):
    names = []
    total_qty = 0
    for item in items:
        names.append(item["name"])
    total_qty = sum(item["qty"] for item in items)
    label = ", ".join(names)
    width = len(label)
    return label, total_qty, width


def summarize_again(items):
    names = []
    total_qty = 0
    for item in items:
        names.append(item["name"])
    total_qty = sum(item["qty"] for item in items)
    label = ", ".join(names)
    width = len(label)
    return label, total_qty, width
'''

SYNTHETIC = [
    {
        "app.py": _APP,
        "shop/__init__.py": "",
        "shop/cart.py": _CART_V1,
        "shop/pricing.py": _PRICING_V1,
        "tests/test_cart.py": _TEST,
        "README.md": "# shop\n",
    },
    {
        "app.py": _APP,
        "shop/__init__.py": "",
        "shop/cart.py": _CART_V2,
        "shop/util.py": _UTIL_V2,
        "shop/pricing.py": _PRICING_V1.replace("return subtotal\n", "return round(subtotal, 2)\n"),
        "tests/test_cart.py": _TEST,
        "README.md": "# shop\n",
    },
    {
        "app.py": _APP,
        "shop/__init__.py": "",
        "shop/cart.py": _CART_V3,
        "shop/util.py": _UTIL_V2,
        "shop/pricing.py": _PRICING_V1.replace("return subtotal\n", "return round(subtotal, 3)\n"),
        "tests/test_cart.py": _TEST,
        "README.md": "# shop\n",
    },
]
_FEATURES_MAIN = '''"""Entry point exercising import forms."""

import json as j
import os.path
import pkg.sub.leaf as leaf_mod
from pkg import tools
from pkg import helpers as h
from pkg.sub import leaf, deep
from pkg.models import *
from pkg.models import Base as B, Child

type Pair = tuple[int, int]


def main(*args, **kwargs):
    child = Child(1)
    helper = h.helper
    value = helper(child.run()) + tools.tool() + leaf.leaf_fn() + leaf_mod.leaf_fn()
    other = deep.deep_fn(os.path.join("a", "b"))
    return j.dumps([value, other, B.make(), missing_fn()])


@staticmethod
def decorated(x: int = 1, *, y: str, z=None) -> int:
    def nested(q):
        return q
    return nested(x)
'''

_FEATURES_MODELS = '''"""Class hierarchy with self and super calls."""

from .helpers import helper as assist


class Base:
    def __init__(self, value):
        self.value = value

    def run(self):
        return self.step() + assist(self.value)

    def step(self):
        return 1

    @classmethod
    def make(cls):
        return cls(0)


class Child(Base):
    def run(self):
        return super().run() + self.extra()

    def extra(self):
        local = Base(2)
        return local.step() if self.value else 0

    class Inner:
        def inner(self):
            return 0
'''

_FEATURES_HELPERS = """from . import tools
from .sub import leaf as sub_leaf


def helper(value: int, /, scale: float = 1.0, *rest: int, flag: bool = False, **extra) -> float:
    return value * scale + tools.tool() + sub_leaf.leaf_fn()
"""

_FEATURES_TOOLS = """def tool():
    return helper_twice()


def helper_twice():
    return tool() if False else 2
"""

_FEATURES_LEAF = """from .. import tools
from ..models import Base


def leaf_fn():
    return tools.tool() + Base(1).step()
"""

_FEATURES_DEEP = """from ...pkg import tools
from . import leaf


def deep_fn(path):
    if path and len(path) > 1 and path.startswith("a") or path.endswith("z"):
        return leaf.leaf_fn()
    return [p for p in path if p] and tools.tool()
"""

FEATURES = [
    {
        "main.py": _FEATURES_MAIN,
        "pkg/__init__.py": "",
        "pkg/models.py": _FEATURES_MODELS,
        "pkg/helpers.py": _FEATURES_HELPERS,
        "pkg/tools.py": _FEATURES_TOOLS,
        "pkg/sub/__init__.py": "from . import leaf\n",
        "pkg/sub/leaf.py": _FEATURES_LEAF,
        "pkg/sub/deep.py": _FEATURES_DEEP,
        "pkg/broken.py": "def broken(:\n    pass\n",
        "pkg/venv/ignored.py": "x = 1\n",
        "pkg/empty.py": "   \n",
    },
    {
        "main.py": _FEATURES_MAIN.replace("return nested(x)", "return nested(x) + 1"),
        "pkg/__init__.py": "",
        "pkg/models.py": _FEATURES_MODELS.replace("return 1\n", "return 2\n"),
        "pkg/helpers.py": _FEATURES_HELPERS,
        "pkg/tools.py": _FEATURES_TOOLS,
        "pkg/sub/__init__.py": "from . import leaf\n",
        "pkg/sub/leaf.py": _FEATURES_LEAF,
        "pkg/sub/deep.py": _FEATURES_DEEP,
        "pkg/empty.py": "   \n",
    },
]
EMPTY_PRODUCTION = [{"tests/test_only.py": _TEST, "notes.txt": "nothing here\n"}]


# --------------------------------------------------------------------------- fork plumbing
def load_fork(root: Path) -> Any:
    """The fork's scoring entry points (imported from its source tree) and its commit."""
    sys.path.insert(0, str(root / "src"))
    from slop_code import common
    from slop_code.metrics.driver import measure_snapshot_quality
    from slop_code.metrics.languages.python.graph import (
        build_dependency_graph,
        dependency_graph_traversal,
    )
    from slop_code.metrics.quality_io import save_quality_metrics
    from slop_code.metrics.scoring import live_evidence, live_regression, production
    from slop_code.metrics.scoring import models as scoring_models
    from slop_code.metrics.scoring.inventory import build_inventory
    from slop_code.metrics.scoring.lineage import (
        calculate_problem_rework,
        calculate_transition_rework,
    )
    from slop_code.metrics.scoring.regression import calculate_regression_breadth
    from slop_code.metrics.scoring.schema import calculate_problem_score
    from slop_code.metrics.scoring.sensitivity import (
        component_correlations,
        controlled_fixture_scores,
        weight_ablations,
    )

    return SimpleNamespace(
        common=common,
        measure_snapshot_quality=measure_snapshot_quality,
        build_dependency_graph=build_dependency_graph,
        traversal=dependency_graph_traversal,
        save_quality_metrics=save_quality_metrics,
        live_evidence=live_evidence,
        live_regression=live_regression,
        production=production,
        models=scoring_models,
        build_inventory=build_inventory,
        calculate_problem_rework=calculate_problem_rework,
        calculate_transition_rework=calculate_transition_rework,
        calculate_regression_breadth=calculate_regression_breadth,
        calculate_problem_score=calculate_problem_score,
        component_correlations=component_correlations,
        controlled_fixture_scores=controlled_fixture_scores,
        weight_ablations=weight_ablations,
        commit=subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
    )


Fork = Any


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


_SYMBOL_FIELDS = (
    "file_path",
    "name",
    "type",
    "start",
    "end",
    "complexity",
    "sloc",
    "parent_class",
    "body_hash",
    "structure_hash",
    "signature_hash",
)


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _checkpoint(fork: Fork, work: Path, name: str, files: dict[str, str], entry: str) -> dict:
    checkpoint_dir = work / name
    snapshot = checkpoint_dir / fork.common.SNAPSHOT_DIR_NAME
    _write_tree(snapshot, files)
    inventory = fork.build_inventory(snapshot)
    snapshot_metrics, file_metrics = fork.measure_snapshot_quality(entry, snapshot)
    fork.save_quality_metrics(checkpoint_dir, snapshot_metrics, file_metrics)
    quality_dir = checkpoint_dir / fork.common.QUALITY_DIR
    file_rows = _rows(quality_dir / fork.common.FILES_QUALITY_SAVENAME)
    # Stable sort by file: the fork's own walk follows directory-listing order, which
    # differs between filesystems; Raidar walks files in sorted order.
    symbol_rows = sorted(
        _rows(quality_dir / fork.common.SYMBOLS_QUALITY_SAVENAME),
        key=lambda row: row["file_path"].encode(),
    )
    graph = fork.build_dependency_graph(snapshot, snapshot / entry)
    nodes, edges = fork.traversal(graph)
    produced = fork.production.produce_production_evidence(
        name, snapshot, inventory, file_rows, symbol_rows, graph, Path(sys.executable)
    )
    return {
        "checkpoint": name,
        "files": files,
        "roles": {item.path: item.role.value for item in inventory.files},
        "file_rows": sorted(
            ({"file_path": row["file_path"], "success": row["success"]} for row in file_rows),
            key=lambda row: row["file_path"],
        ),
        "symbol_rows": sorted(
            ({key: row.get(key) for key in _SYMBOL_FIELDS} for row in symbol_rows),
            key=lambda row: (row["file_path"], row["start"], row["end"], row["name"], row["type"]),
        ),
        "graph": {
            "nodes": [{"identity": node.identity, "path": node.path} for node in nodes],
            "edges": [
                {"source": edge.source, "target": edge.target, "weight": edge.weight}
                for edge in edges
            ],
        },
        "production": produced.model_dump(mode="json"),
        "lineage_symbols": [
            symbol.model_dump(mode="json")
            for symbol in fork.live_regression._current_production_symbols(checkpoint_dir)
        ],
        "rework_symbols": _rework_symbols(fork, produced),
    }


def _rework_symbols(fork: Fork, produced: Any) -> list[dict] | None:
    """The fork's live R projection (``live_evidence._symbols_from_raw``): unhashed V/E symbols."""
    if produced.production_quality is None:
        return None
    raw = {"p/production_quality.json": produced.production_quality.model_dump_json().encode()}
    return [
        symbol.model_dump(mode="json") for symbol in fork.live_evidence._symbols_from_raw(raw, "p")
    ]


def _lineage(fork: Fork, work: Path, checkpoints: list[dict]) -> dict | None:
    """R exactly as the fork's live producer (``live_evidence._produce_rework_evidence``)."""
    if any(checkpoint["rework_symbols"] is None for checkpoint in checkpoints):
        return None  # the fork's canonical_provenance_unavailable
    snapshot = fork.common.SNAPSHOT_DIR_NAME
    inputs = []
    for index, checkpoint in enumerate(checkpoints):
        symbols = tuple(
            fork.models.SymbolIdentity.model_validate(row) for row in checkpoint["rework_symbols"]
        )
        removed: tuple = ()
        added: tuple = ()
        if index:
            prior = inputs[-1]
            removed, added = fork.live_evidence._changed_lines(
                work / checkpoints[index - 1]["checkpoint"] / snapshot,
                work / checkpoint["checkpoint"] / snapshot,
                (symbol.path for symbol in (*prior.symbols, *symbols)),
            )
        inputs.append(
            fork.models.ReworkCheckpointInput(
                checkpoint_id=checkpoint["checkpoint"],
                symbols=symbols,
                removed_lines=removed,
                added_lines=added,
            )
        )
    return fork.calculate_problem_rework(inputs).model_dump(mode="json")


def _regressions(
    fork: Fork, work: Path, checkpoints: list[dict], lineage: dict, runs: dict[str, dict]
) -> dict[str, dict]:
    results = {}
    for index in range(1, len(checkpoints)):
        prior, current = checkpoints[index - 1]["checkpoint"], checkpoints[index]["checkpoint"]
        if current not in runs:
            continue
        changed = fork.live_regression._changed_production_symbols(work / prior, work / current)
        path = "lineage.json"
        accepted = fork.live_regression._accepted_changed_current_symbols(
            {path: json.dumps(lineage["transitions"][index - 1]).encode()}, path
        )
        scope = {symbol.stable_identity: symbol for symbol in (*changed, *accepted)}
        evidence = fork.models.RegressionBreadthInput(
            transition_id=f"{prior}--{current}",
            baseline=fork.models.RegressionRunEvidence.model_validate(runs[current]["baseline"]),
            produced=fork.models.RegressionRunEvidence.model_validate(runs[current]["produced"]),
            changed_symbols=tuple(sorted(scope.values(), key=lambda item: item.stable_identity)),
        )
        results[f"{prior}->{current}"] = without_runs(
            fork.calculate_regression_breadth(evidence).model_dump(mode="json")
        )
    return results


def without_runs(attribution: dict[str, Any]) -> dict[str, Any]:
    """An attribution without its (already stored) run evidence copies."""
    return {
        **attribution,
        "input": {
            key: value
            for key, value in attribution["input"].items()
            if key not in {"baseline", "produced"}
        },
    }


def _case(
    fork: Fork, name: str, snapshots: list[dict[str, str]], entry: str, runs: dict[str, dict]
) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"fork-goldens-{name}-") as temporary:
        # Resolved, as the fork's live producer resolves checkpoint directories.
        work = Path(temporary).resolve()
        checkpoints = [
            _checkpoint(fork, work, f"checkpoint_{index}", files, entry)
            for index, files in enumerate(snapshots, start=1)
        ]
        lineage = _lineage(fork, work, checkpoints) if len(checkpoints) > 1 else None
        regressions = _regressions(fork, work, checkpoints, lineage, runs) if lineage else {}
    return {
        "name": name,
        "entry_file": entry,
        "checkpoints": checkpoints,
        "runs": runs,
        "lineage": lineage,
        "regressions": regressions,
    }


# --------------------------------------------------------------------------- run evidence
def _environment(label: str) -> dict[str, str]:
    return {
        key: _h(f"{label}:{key}")
        for key in (
            "environment_id",
            "project_sha256",
            "lock_sha256",
            "platform_sha256",
            "plugin_sha256",
            "interpreter_sha256",
        )
    }


def _run(
    outcomes: list[tuple[str, str, str]], coverage: list[dict], env: str = "env"
) -> dict[str, Any]:
    return {
        "environment": _environment(env),
        "corpus": {"corpus_id": _h("corpus"), "manifest_sha256": _h("manifest")},
        "invocation": {"argv": ["pytest", "-q"], "cwd": "/workspace"},
        "outcomes": [
            {"node_id": node, "group_type": group, "phase": "call", "outcome": outcome}
            for node, group, outcome in outcomes
        ],
        "ledger": [
            {"node_id": node, "phase": "call", "outcome": outcome} for node, _, outcome in outcomes
        ],
        "coverage": coverage,
        "process_events": [],
    }


def _lines_of(checkpoint: dict, path: str, name: str) -> list[int]:
    row = next(
        row
        for row in checkpoint["symbol_rows"]
        if row["file_path"] == path
        and row["name"] == name
        and row["type"] in {"function", "method"}
    )
    return list(range(row["start"], row["end"] + 1))


def synthetic_runs(fork: Fork) -> dict[str, dict]:
    """cp2: a failing regression covering changed code; cp3: a parity mismatch."""
    with tempfile.TemporaryDirectory(prefix="fork-goldens-lines-") as temporary:
        work = Path(temporary).resolve()
        cp2 = _checkpoint(fork, work, "checkpoint_2", SYNTHETIC[1], SYNTHETIC_ENTRY)
    outcomes = [
        ("tests/test_cart.py::test_load", "Regression", "failed"),
        ("tests/test_cart.py::test_parse[a]", "Regression", "passed"),
        ("tests/test_cart.py::test_new", "Core", "failed"),
    ]
    coverage = [
        {
            "node_id": "tests/test_cart.py::test_load",
            "path": "shop/cart.py",
            "executed_lines": sorted(
                {*_lines_of(cp2, "shop/cart.py", "count"), *_lines_of(cp2, "shop/cart.py", "load")}
            ),
        },
        {
            "node_id": "tests/test_cart.py::test_load",
            "path": "shop/util.py",
            "executed_lines": _lines_of(cp2, "shop/util.py", "clamp"),
        },
        {
            "node_id": "tests/test_cart.py::test_new",
            "path": "shop/cart.py",
            "executed_lines": _lines_of(cp2, "shop/cart.py", "parse_item"),
        },
    ]
    baseline = _run(outcomes, [])
    produced = _run(outcomes, coverage)
    mismatch = _run(outcomes, [], env="other-env")
    return {
        "checkpoint_2": {"baseline": baseline, "produced": produced},
        "checkpoint_3": {"baseline": mismatch, "produced": _run(outcomes, [])},
    }


def forge_runs(trial: Path | None) -> dict[str, dict]:
    if trial is None:
        return {}
    runs = {}
    for path in sorted((trial / "raidar-oracle" / "steps").glob("*/g-kernel-input.json")):
        payload = json.loads(path.read_text())
        runs[path.parent.name] = {"baseline": payload["baseline"], "produced": payload["produced"]}
    return runs


def forge_snapshots(root: Path) -> list[dict[str, str]]:
    return [
        {
            path.relative_to(checkpoint).as_posix(): path.read_text()
            for path in sorted(checkpoint.rglob("*"))
            if path.is_file()
        }
        for checkpoint in sorted(root.iterdir(), key=lambda item: int(item.name.split("_")[1]))
    ]


# --------------------------------------------------------------------------- kernel scenarios
def _symbol(name: str, **fields: Any) -> dict[str, Any]:
    start = fields.pop("start", 10)
    body, structure, signature = (fields.pop(key, "") for key in ("body", "structure", "signature"))
    return {
        "path": fields.pop("path", "src/mod.py"),
        "qualified_name": name,
        "parent_qualified_name": fields.pop("parent", ""),
        "kind": fields.pop("kind", "function"),
        "start_line": start,
        "end_line": fields.pop("end", start + 2),
        "language": "python",
        "body_sha256": _h(body) if body else "",
        "structure_sha256": _h(structure) if structure else "",
        "signature_sha256": _h(signature) if signature else "",
    }


def _lines(side: str, path: str, *numbers: int) -> list[dict[str, Any]]:
    return [{"path": path, "line": line, "side": side} for line in numbers]


def lineage_scenarios() -> list[dict[str, Any]]:
    exact_prior = _symbol("f", start=1, end=3)
    split_prior = _symbol("split", body="copy", structure="tree", start=1, end=4)
    merge_a = _symbol("merge_a", body="copy", structure="tree", start=40, end=43)
    merge_b = _symbol("merge_b", body="copy", structure="tree", start=50, end=53)
    outer = _symbol("outer", start=1, end=20, body="body", structure="tree")
    return [
        {
            "name": "hash_flags_signature_only",
            "prior": [_symbol("old", signature="same")],
            "current": [_symbol("new", signature="same")],
            "removed": [],
            "added": [],
            "ledger": [],
        },
        {
            "name": "exact_rename_move_and_unrelated",
            "prior": [
                exact_prior,
                _symbol("old", body="rb", structure="rt", start=10),
                _symbol("moved", path="src/old.py", body="mb", structure="mt"),
            ],
            "current": [
                _symbol("f", start=4, end=6, body="edited"),
                _symbol("new", body="rb", structure="rt", start=20),
                _symbol("moved", path="src/new.py", body="mb", structure="mt"),
                _symbol("renamed", body="other", structure="other"),
            ],
            "removed": _lines("removed", "src/mod.py", 1),
            "added": _lines("added", "src/mod.py", 4),
            "ledger": [exact_prior],
        },
        {
            "name": "split_is_ambiguous",
            "prior": [split_prior],
            "current": [
                _symbol("split_a", body="copy", structure="tree", start=20, end=23),
                _symbol("split_b", body="copy", structure="tree", start=30, end=33),
            ],
            "removed": _lines("removed", "src/mod.py", 2),
            "added": _lines("added", "src/mod.py", 21, 31),
            "ledger": [split_prior],
        },
        {
            "name": "merge_is_ambiguous",
            "prior": [merge_a, merge_b],
            "current": [_symbol("merge", body="copy", structure="tree", start=60, end=63)],
            "removed": _lines("removed", "src/mod.py", 41, 51),
            "added": _lines("added", "src/mod.py", 61),
            "ledger": [merge_a],
        },
        {
            "name": "nested_ownership_is_innermost",
            "prior": [
                outer,
                _symbol("inner", parent="outer", kind="method", start=5, end=9, body="b"),
            ],
            "current": [
                _symbol("outer", start=1, end=22, body="body", structure="tree"),
                _symbol("inner", parent="outer", kind="method", start=5, end=11, body="b2"),
            ],
            "removed": _lines("removed", "src/mod.py", 6, 15),
            "added": _lines("added", "src/mod.py", 6, 7, 16, 21),
            "ledger": [outer],
        },
    ]


def regression_scenarios() -> list[dict[str, Any]]:
    changed = [
        _symbol("outer", path="src/a.py", start=1, end=30),
        _symbol("inner", path="src/a.py", start=5, end=10),
        _symbol("other", path="src/b.py", start=1, end=5),
    ]
    outcomes = [
        ("t.py::test_a[1]", "Regression", "failed"),
        ("t.py::test_b", "Regression", "error"),
        ("t.py::test_c", "Regression", "passed"),
        ("t.py::test_d", "Core", "failed"),
    ]
    coverage = [
        {"node_id": "t.py::test_a[1]", "path": "src/a.py", "executed_lines": [2, 6, 7]},
        {"node_id": "t.py::test_b", "path": "src/b.py", "executed_lines": [99]},
        {"node_id": "t.py::test_c", "path": "src/b.py", "executed_lines": [2]},
        {"node_id": "t.py::test_d", "path": "src/b.py", "executed_lines": [3]},
    ]
    attributed = {"baseline": _run(outcomes, []), "produced": _run(outcomes, coverage)}
    event = {"node_id": "t.py::test_b", "phase": "call", "event": "subprocess.Popen"}
    with_event = {
        "baseline": {**_run(outcomes, []), "process_events": [event]},
        "produced": {**_run(outcomes, coverage), "process_events": [event]},
    }
    return [
        {"name": "attributes_innermost_changed_symbol", "changed": changed, **attributed},
        {"name": "no_changed_symbols_scores_one", "changed": [], **attributed},
        {
            "name": "environment_mismatch_is_ineligible",
            "changed": changed,
            "baseline": _run(outcomes, [], env="x"),
            "produced": _run(outcomes, coverage),
        },
        {
            "name": "uncovered_process_event_is_ineligible",
            "changed": changed,
            **with_event,
        },
    ]


def _kernels(fork: Fork) -> dict[str, Any]:
    models = fork.models
    lineage = []
    for scenario in lineage_scenarios():
        ledger = {
            models.SymbolIdentity.model_validate(symbol).stable_identity: True
            for symbol in scenario["ledger"]
        }
        result = fork.calculate_transition_rework(
            [models.SymbolIdentity.model_validate(symbol) for symbol in scenario["prior"]],
            [models.SymbolIdentity.model_validate(symbol) for symbol in scenario["current"]],
            [models.PhysicalLineEvidence.model_validate(line) for line in scenario["removed"]],
            [models.PhysicalLineEvidence.model_validate(line) for line in scenario["added"]],
            ledger,
        )
        lineage.append({**scenario, "result": result.model_dump(mode="json")})
    regression = []
    for scenario in regression_scenarios():
        evidence = models.RegressionBreadthInput.model_validate(
            {
                "transition_id": scenario["name"],
                "baseline": scenario["baseline"],
                "produced": scenario["produced"],
                "changed_symbols": scenario["changed"],
            }
        )
        result = fork.calculate_regression_breadth(evidence)
        regression.append({**scenario, "result": result.model_dump(mode="json")})
    return {"lineage": lineage, "regression": regression}


# --------------------------------------------------------------------------- formulas
def _correctness(fork: Fork, rates: list[str]) -> list[Any]:
    counts = {"0": (0, 1), "0.5": (1, 2), "1": (1, 1)}
    return [
        fork.models.CheckpointCorrectnessEvidence(
            checkpoint_id=f"checkpoint-{index}",
            passed=counts.get(rate, (1, 3))[0],
            total=counts.get(rate, (1, 3))[1],
            rate=Decimal(rate),
        )
        for index, rate in enumerate(rates, start=1)
    ]


def _formulas(fork: Fork, root: Path) -> dict[str, Any]:
    golden = json.loads((root / "tests/metrics/fixtures/scoring_golden.json").read_text())
    names = ("verbosity", "erosion", "architecture", "rework", "regression")
    problems = []
    for case in golden["problem_cases"]:
        score = fork.calculate_problem_score(
            case["name"],
            _correctness(fork, case["checkpoint_correctness"]),
            *(tuple(Decimal(value) for value in case[name]) for name in names),
        )
        problems.append({**case, "result": score.model_dump(mode="json")})
    controlled = fork.controlled_fixture_scores(Decimal("0.25"))
    scores = [
        fork.models.ProblemScore.model_validate(problem["result"]) for problem in problems[:8]
    ]
    renamed = [score.model_copy(update={"problem_id": f"p{i}"}) for i, score in enumerate(scores)]
    return {
        "problems": problems,
        "controlled_decrement": "0.25",
        "controlled": {name: score.model_dump(mode="json") for name, score in controlled.items()},
        "ablations": {
            name: format(value, "f")
            for name, value in fork.weight_ablations(controlled.values(), renamed).items()
        },
        "correlation_scores": [score.model_dump(mode="json") for score in renamed],
        "correlations": _correlations(fork, renamed),
    }


def _correlations(fork: Fork, scores: list[Any]) -> dict[str, str] | str:
    try:
        values = fork.component_correlations(scores)
    except ValueError as error:
        return f"error: {error}"
    return {f"{left}|{right}": format(value, "f") for (left, right), value in values.items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fork", type=Path, required=True)
    parser.add_argument("--forge", type=Path, required=True)
    parser.add_argument("--forge-runs", type=Path)
    args = parser.parse_args()
    fork = load_fork(args.fork)
    goldens = {
        "fork_commit": fork.commit,
        "cases": [
            _case(fork, "synthetic_shop", SYNTHETIC, SYNTHETIC_ENTRY, synthetic_runs(fork)),
            _case(fork, "no_production_code", EMPTY_PRODUCTION, SYNTHETIC_ENTRY, {}),
            _case(fork, "language_features", FEATURES, "main.py", {}),
            _case(
                fork, "forge", forge_snapshots(args.forge), "forge.py", forge_runs(args.forge_runs)
            ),
        ],
        "kernels": _kernels(fork),
        "formulas": _formulas(fork, args.fork),
    }
    payload = json.dumps(goldens, sort_keys=True, separators=(",", ":")).encode()
    with OUTPUT.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as handle:
        handle.write(payload)


if __name__ == "__main__":
    main()
