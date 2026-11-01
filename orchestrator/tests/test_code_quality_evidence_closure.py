"""Production evidence refusals: V/E inputs, the call graph and what a scorer does not count."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raidar.scoring.code_quality.architecture import (
    ProductionGraphError,
    produce_production_graph,
)
from raidar.scoring.code_quality.evidence import FileDigests, repeated_line_keys, ruff_evidence
from raidar.scoring.code_quality.lineage_models import TransitionReworkEvidence
from raidar.scoring.code_quality.quality import ProductionQualityError, produce_production_quality
from raidar.scoring.code_quality.snapshot_tool import cli
from raidar.scoring.framework.inventory import build_inventory
from tests.code_quality_support import (
    fork_goldens,
    in_memory_tools,
    materialise,
    ok_result,
    scripted_tools,
)


def _traversal(nodes: list[tuple[str, str]], edges: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "nodes": [{"identity": identity, "path": path} for identity, path in nodes],
        "edges": [{"source": s, "target": t, "weight": 2} for s, t in edges],
    }


_ROLES = [
    {"path": "a.py", "role": "production"},
    {"path": "t.py", "role": "test"},
    {"path": "u.py", "role": "test"},
]


def test_edges_between_non_production_files_are_not_counted() -> None:
    traversal = _traversal(
        [("a.py::f", "a.py"), ("t.py::t", "t.py"), ("u.py::u", "u.py")],
        [("t.py::t", "u.py::u")],
    )

    result = produce_production_graph("checkpoint_1", _ROLES, traversal)

    assert len(result.evidence.edges) == 0
    assert len(result.evidence.cross_role_edges) == 0


def test_a_production_graph_with_no_edges_is_perfectly_acyclic() -> None:
    result = produce_production_graph("checkpoint_1", _ROLES, _traversal([("a.py::f", "a.py")], []))

    assert result.architecture == Decimal(1)
    assert result.evidence.total_mass == Decimal(0)


def test_inventory_rows_without_a_path_are_refused_whatever_their_shape() -> None:
    for row in ({"role": "production"}, SimpleNamespace(role="production")):
        with pytest.raises(ProductionGraphError, match="lexical file identity"):
            produce_production_graph("checkpoint_1", [row], _traversal([], []))
    attribute_row = SimpleNamespace(path="a.py", role="production")
    result = produce_production_graph("checkpoint_1", [attribute_row], _traversal([], []))
    assert result.architecture == Decimal(1)


@pytest.fixture(name="shop")
def _shop(tmp_path: Path) -> dict[str, Any]:
    snapshot = materialise(
        tmp_path / "snapshot",
        {
            "app.py": "def entry():\n    value = 1\n    return value\n",
            "other.py": "def other():\n    return 2\n",
        },
    )
    measured = cli.measure(snapshot, "app.py")
    return {
        "snapshot": snapshot,
        "inventory": build_inventory(snapshot).files,
        "files": measured["files"],
        "symbols": measured["symbols"],
    }


def _produce(shop: dict[str, Any], tools: Any = None, **overrides: Any) -> Any:
    inputs = {**shop, **overrides}
    return produce_production_quality(
        "checkpoint_1",
        inputs["snapshot"],
        inputs["inventory"],
        inputs["files"],
        inputs["symbols"],
        tools or in_memory_tools(),
    )


def test_an_inventory_row_without_a_path_or_with_an_unencodable_one_is_refused(
    shop: dict[str, Any],
) -> None:
    for row in (
        {"role": "production"},
        {"path": "\ud800.py", "role": "production"},
    ):
        with pytest.raises(ProductionQualityError, match="invalid lexical file identity"):
            _produce(shop, inventory=[*shop["inventory"], row])


def test_measured_rows_without_a_path_are_not_production_file_rows(shop: dict[str, Any]) -> None:
    baseline = _produce(shop)

    result = _produce(shop, files=[*shop["files"], {"success": True}, {"file_path": 3}])

    assert result.verbosity == baseline.verbosity


def test_a_production_tree_without_source_lines_has_no_verbosity(tmp_path: Path) -> None:
    snapshot = materialise(tmp_path / "snap", {"app.py": "# only a comment\n"})
    inventory = build_inventory(snapshot).files
    files = [{"file_path": "app.py", "success": True}]

    with pytest.raises(ProductionQualityError, match="^production_source_loc_zero$"):
        produce_production_quality("c1", snapshot, inventory, files, [], in_memory_tools())


def test_a_diagnostic_for_a_file_the_evaluator_did_not_measure_is_refused(
    shop: dict[str, Any],
) -> None:
    sources = {
        item.path: (shop["snapshot"] / item.path).read_bytes()
        for item in shop["inventory"]
        if item.path.endswith(".py")
    }
    measured = json.loads(in_memory_tools().evaluate(sources).stdout)
    measured["files"] = [row for row in measured["files"] if row["path"] != "app.py"]
    diagnostic = json.dumps(
        [
            {
                "code": "SIM108",
                "filename": "app.py",
                "location": {"row": 1, "column": 1},
                "end_location": {"row": 1, "column": 5},
                "message": "m",
            }
        ]
    )
    tools = scripted_tools(
        (lambda argv: "evaluate" in argv, ok_result(json.dumps(measured))),
        (lambda argv: "check" in argv, ok_result(diagnostic, returncode=1)),
    )

    with pytest.raises(ProductionQualityError, match="outside production inventory"):
        _produce(shop, tools)


def test_each_ruff_diagnostic_becomes_evidence_pointing_at_its_file(shop: dict[str, Any]) -> None:
    diagnostic = json.dumps(
        [
            {
                "code": "SIM108",
                "filename": "app.py",
                "location": {"row": 2, "column": 1},
                "end_location": {"row": 3, "column": 5},
                "message": "m",
            }
        ]
    )
    tools = scripted_tools((lambda argv: "check" in argv, ok_result(diagnostic, returncode=1)))
    quality = _produce(shop, tools)

    (item,) = ruff_evidence(
        FileDigests(), "lint_findings", "checkpoint_1", shop["snapshot"], quality
    )

    assert (item.metric, item.path, item.kind) == ("lint_findings", "app.py", "ruff:SIM108")
    assert [(span.line, span.end_line) for span in item.spans] == [(2, 3)]


def _kernel(name: str) -> TransitionReworkEvidence:
    scenario = next(s for s in fork_goldens()["kernels"]["lineage"] if s["name"] == name)
    return TransitionReworkEvidence.model_validate(scenario["result"])


def test_ambiguous_lineage_repeats_earlier_modification_only_when_a_member_was_modified() -> None:
    # A split whose members were modified before: every changed line of the set counts.
    split = _kernel("split_is_ambiguous")
    assert len(repeated_line_keys(split)) == split.repeated_lines == 3
    # A signature-only ambiguity that was never modified before repeats nothing.
    untouched = _kernel("hash_flags_signature_only")
    assert repeated_line_keys(untouched) == set()
    # A merge where only one candidate edge was modified before still counts the set.
    merge = _kernel("merge_is_ambiguous")
    assert len(repeated_line_keys(merge)) == merge.repeated_lines


def test_unrelated_rejected_candidates_never_make_a_line_repeated() -> None:
    for name in ("exact_rename_move_and_unrelated", "nested_ownership_is_innermost"):
        evidence = _kernel(name)
        assert len(repeated_line_keys(evidence)) == evidence.repeated_lines


def test_repeated_rework_evidence_must_reconcile_with_the_kernel_count() -> None:
    split = _kernel("split_is_ambiguous")
    inflated = split.model_copy(update={"repeated_lines": split.repeated_lines + 1})

    with pytest.raises(ValueError, match="does not reconcile"):
        repeated_line_keys(inflated)
