"""G2/G3 golden parity: Raidar's port and the SCB fork produce identical values and evidence.

Every expected value in ``fork_goldens.json.gz`` was produced by the fork's
own code on the stored inputs (see ``fixtures/code_quality``). Raidar replays
those inputs through its ported producers and kernels:

- measurement adapters: file rows, tree-sitter symbol rows and hashes, the
  entrypoint-bounded call graph;
- V/E's eligible-symbol evidence and H graph evidence (the Ruff and clone evidence under the
  real pinned tool env is the slow file's);
- the R lineage kernel and ledger exactly as the fork's live producer feeds
  them (V/E's unhashed eligible symbols and snapshot diffs);
- the G attribution kernel over run evidence and the fork's live changed-symbol
  scope (hashed diff owners united with R's changed accepted lineages);
- the problem arithmetic (with ``scb-canonical@1``).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from raidar.contract import file_io
from raidar.scoring.code_quality import arithmetic
from raidar.scoring.code_quality.diffs import production_symbols
from raidar.scoring.code_quality.lineage import calculate_transition_rework
from raidar.scoring.code_quality.lineage_models import (
    PhysicalLineEvidence,
    RegressionBreadthInput,
    SymbolIdentity,
    TransitionReworkEvidence,
)
from raidar.scoring.code_quality.models import CheckpointCorrectnessEvidence
from raidar.scoring.code_quality.regression import calculate_regression_breadth
from raidar.scoring.code_quality.snapshot_tool.cli import measure
from raidar.scoring.framework.inventory import FileRole, build_inventory
from tests.code_quality_support import (
    assert_lineage_and_regression_equal_the_fork,
    case_snapshots,
    fork_goldens,
    golden_case,
    in_memory_tools,
)

_SYMBOL_ORDER = ("file_path", "start", "end", "name", "type")


def _sorted_symbols(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = (
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
    return sorted(
        ({key: row.get(key) for key in fields} for row in rows),
        key=lambda row: tuple(row[key] for key in _SYMBOL_ORDER),
    )


def _graph_view(graph: dict[str, Any]) -> dict[str, Any]:
    return {
        "nodes": [{"identity": n["identity"], "path": n["path"]} for n in graph["nodes"]],
        "edges": graph["edges"],
    }


@pytest.mark.parametrize("name", ["synthetic_shop", "language_features", "forge"])
def test_measurement_adapters_match_the_fork_rows_symbols_and_call_graph(
    tmp_path: Path, name: str
) -> None:
    case = golden_case(name)
    for snapshot, checkpoint in zip(
        case_snapshots(tmp_path, case), case["checkpoints"], strict=True
    ):
        measured = measure(snapshot, case["entry_file"], file_io)
        inventory = build_inventory(snapshot)
        assert {item.path: item.role.value for item in inventory.files} == checkpoint["roles"]
        assert (
            sorted(
                (
                    {"file_path": row["file_path"], "success": row["success"]}
                    for row in measured["files"]
                ),
                key=lambda row: row["file_path"],
            )
            == checkpoint["file_rows"]
        )
        assert _sorted_symbols(measured["symbols"]) == checkpoint["symbol_rows"]
        assert _graph_view(measured["graph"]) == checkpoint["graph"]
        production = [i.path for i in inventory.files if i.role is FileRole.PRODUCTION]
        assert [
            symbol.model_dump(mode="json")
            for symbol in production_symbols(measured["symbols"], production)
        ] == checkpoint["lineage_symbols"]


def test_rework_is_unavailable_like_the_fork_when_a_checkpoint_has_no_ve_evidence() -> None:
    case = golden_case("language_features")
    assert [item["rework_symbols"] is None for item in case["checkpoints"]].count(True) >= 1
    assert case["lineage"] is None


def test_rework_lineage_and_regression_equal_the_fork_live_producers(tmp_path: Path) -> None:
    assert_lineage_and_regression_equal_the_fork(
        tmp_path, golden_case("synthetic_shop"), in_memory_tools()
    )


def test_lineage_kernel_scenarios_equal_the_fork() -> None:
    for scenario in fork_goldens()["kernels"]["lineage"]:
        ledger = {
            SymbolIdentity.model_validate(symbol).stable_identity: True
            for symbol in scenario["ledger"]
        }
        result = calculate_transition_rework(
            [SymbolIdentity.model_validate(symbol) for symbol in scenario["prior"]],
            [SymbolIdentity.model_validate(symbol) for symbol in scenario["current"]],
            [PhysicalLineEvidence.model_validate(line) for line in scenario["removed"]],
            [PhysicalLineEvidence.model_validate(line) for line in scenario["added"]],
            ledger,
        )
        assert result.model_dump(mode="json") == scenario["result"], scenario["name"]
        assert TransitionReworkEvidence.model_validate(scenario["result"]) == result


def test_regression_kernel_scenarios_equal_the_fork() -> None:
    for scenario in fork_goldens()["kernels"]["regression"]:
        result = calculate_regression_breadth(
            RegressionBreadthInput.model_validate(
                {
                    "transition_id": scenario["name"],
                    "baseline": scenario["baseline"],
                    "produced": scenario["produced"],
                    "changed_symbols": scenario["changed"],
                }
            )
        )
        assert result.model_dump(mode="json") == scenario["result"], scenario["name"]


def _correctness(rates: list[str]) -> list[CheckpointCorrectnessEvidence]:
    counts = {"0": (0, 1), "0.5": (1, 2), "1": (1, 1)}
    return [
        CheckpointCorrectnessEvidence(
            checkpoint_id=f"checkpoint-{index}",
            passed=counts.get(rate, (1, 3))[0],
            total=counts.get(rate, (1, 3))[1],
            rate=Decimal(rate),
        )
        for index, rate in enumerate(rates, start=1)
    ]


def test_problem_arithmetic_through_the_canonical_policy_equals_the_fork_scores() -> None:
    names = ("verbosity", "erosion", "architecture", "rework", "regression")
    for case in fork_goldens()["formulas"]["problems"]:
        score = arithmetic.calculate_problem_score(
            case["name"],
            _correctness(case["checkpoint_correctness"]),
            *(tuple(Decimal(value) for value in case[name]) for name in names),
        )
        assert score.model_dump(mode="json") == case["result"], case["name"]
        assert str(score.score) == case["score"]
