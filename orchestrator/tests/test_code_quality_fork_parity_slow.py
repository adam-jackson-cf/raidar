"""G2/G3 golden parity: Raidar's port and the SCB fork produce identical values and evidence.

Every expected value in ``fork_goldens.json.gz`` was produced by the fork's
own code on the stored inputs (see ``fixtures/code_quality``). Raidar replays
those inputs through its ported producers and kernels:

- measurement adapters: file rows, tree-sitter symbol rows and hashes, the
  entrypoint-bounded call graph;
- V/E raw evidence (Ruff contract, clone detector, erosion mass) under the
  real pinned tool env, and H graph evidence;
- the R lineage kernel and ledger exactly as the fork's live producer feeds
  them (V/E's unhashed eligible symbols and snapshot diffs);
- the G attribution kernel over run evidence and the fork's live changed-symbol
  scope (hashed diff owners united with R's changed accepted lineages);
- the problem arithmetic (with ``scb-canonical@1``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from raidar.contract import file_io
from raidar.scoring.code_quality.architecture import produce_production_graph
from raidar.scoring.code_quality.checkpoint import eligibility_code
from raidar.scoring.code_quality.quality import ProductionQualityError, produce_production_quality
from raidar.scoring.code_quality.snapshot_tool.cli import measure
from raidar.scoring.framework.inventory import build_inventory
from tests.code_quality_support import (
    assert_lineage_and_regression_equal_the_fork,
    case_snapshots,
    golden_case,
    quality_tools,
    without_executable,
)

pytestmark = pytest.mark.slow


def test_the_tool_env_subprocess_measures_exactly_what_the_in_process_port_does(
    tmp_path: Path,
) -> None:
    case = golden_case("synthetic_shop")
    snapshot = case_snapshots(tmp_path, case)[-1]
    assert quality_tools().measure(snapshot, case["entry_file"]) == measure(
        snapshot, case["entry_file"], file_io
    )


@pytest.mark.parametrize("name", ["synthetic_shop", "language_features", "forge"])
def test_verbosity_erosion_and_architecture_evidence_equal_the_fork(
    tmp_path: Path, name: str
) -> None:
    case = golden_case(name)
    for snapshot, checkpoint in zip(
        case_snapshots(tmp_path, case), case["checkpoints"], strict=True
    ):
        inventory = build_inventory(snapshot)
        measured = measure(snapshot, case["entry_file"], file_io)
        expected = checkpoint["production"]
        _assert_quality_parity(checkpoint, snapshot, inventory, measured)
        graph = produce_production_graph(
            checkpoint["checkpoint"], inventory.files, measured["graph"]
        )
        assert graph.evidence.model_dump(mode="json") == expected["production_graph"]


def _assert_quality_parity(
    checkpoint: dict[str, Any], snapshot: Path, inventory: Any, measured: dict[str, Any]
) -> None:
    expected = checkpoint["production"]
    arguments = (
        checkpoint["checkpoint"],
        snapshot,
        inventory.files,
        measured["files"],
        measured["symbols"],
        quality_tools(),
    )
    if expected["production_quality"] is None:
        with pytest.raises(ProductionQualityError) as raised:
            produce_production_quality(*arguments)
        assert [eligibility_code(raised.value)] == expected["eligibility"]["reasons"]
        return
    assert expected["eligibility"] == {"eligible": True, "reasons": []}
    quality = produce_production_quality(*arguments)
    expected_quality = expected["production_quality"]
    expected_ruff = expected_quality["ruff"]
    expected_invocation = expected_ruff["invocation"]
    isolated_expected = {
        **expected_quality,
        "ruff": {
            **expected_ruff,
            "invocation": [expected_invocation[0], "-I", "-B", *expected_invocation[1:]],
        },
    }
    assert without_executable(quality.evidence.model_dump(mode="json")) == without_executable(
        isolated_expected
    )


def test_a_snapshot_without_production_source_is_ineligible_like_the_fork(
    tmp_path: Path,
) -> None:
    case = golden_case("no_production_code")
    [checkpoint] = case["checkpoints"]
    [snapshot] = case_snapshots(tmp_path, case)
    measured = measure(snapshot, case["entry_file"], file_io)
    assert checkpoint["production"]["eligibility"]["reasons"] == ["production_source_loc_zero"]
    with pytest.raises(ProductionQualityError, match="^production_source_loc_zero$"):
        produce_production_quality(
            "checkpoint_1",
            snapshot,
            build_inventory(snapshot).files,
            measured["files"],
            measured["symbols"],
            quality_tools(),
        )
    assert measured["graph"] == {"nodes": [], "edges": []}


def test_rework_lineage_and_regression_equal_the_fork_over_the_large_case_with_the_real_tools(
    tmp_path: Path,
) -> None:
    assert_lineage_and_regression_equal_the_fork(tmp_path, golden_case("forge"), quality_tools())
