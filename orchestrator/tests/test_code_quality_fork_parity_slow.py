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

from raidar.scoring.code_quality.architecture import produce_production_graph
from raidar.scoring.code_quality.checkpoint import eligibility_code
from raidar.scoring.code_quality.diffs import (
    changed_lines,
    production_symbols,
    regression_scope,
    rework_symbols,
)
from raidar.scoring.code_quality.lineage import calculate_problem_rework
from raidar.scoring.code_quality.lineage_models import (
    PhysicalLineEvidence,
    RegressionBreadthInput,
    ReworkCheckpointInput,
    SymbolIdentity,
)
from raidar.scoring.code_quality.quality import ProductionQualityError, produce_production_quality
from raidar.scoring.code_quality.regression import calculate_regression_breadth
from raidar.scoring.code_quality.snapshot_tool.cli import measure
from raidar.scoring.framework.inventory import FileRole, build_inventory
from tests.code_quality_support import (
    FORK_CASES,
    case_snapshots,
    golden_case,
    quality_tools,
    without_executable,
    without_runs,
)

pytestmark = pytest.mark.slow


def test_the_tool_env_subprocess_measures_exactly_what_the_in_process_port_does(
    tmp_path: Path,
) -> None:
    case = golden_case("synthetic_shop")
    snapshot = case_snapshots(tmp_path, case)[-1]
    assert quality_tools().measure(snapshot, case["entry_file"]) == measure(
        snapshot, case["entry_file"]
    )


@pytest.mark.parametrize("name", FORK_CASES)
def test_verbosity_erosion_and_architecture_evidence_equal_the_fork(
    tmp_path: Path, name: str
) -> None:
    case = golden_case(name)
    for snapshot, checkpoint in zip(
        case_snapshots(tmp_path, case), case["checkpoints"], strict=True
    ):
        inventory = build_inventory(snapshot)
        measured = measure(snapshot, case["entry_file"])
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
    measured = measure(snapshot, case["entry_file"])
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


def _rework_symbols(snapshot: Path, case: dict[str, Any]) -> tuple[SymbolIdentity, ...]:
    """R's identities as the fork's live producer projects them: V/E's eligible symbols."""
    measured = measure(snapshot, case["entry_file"])
    quality = produce_production_quality(
        "checkpoint",
        snapshot,
        build_inventory(snapshot).files,
        measured["files"],
        measured["symbols"],
        quality_tools(),
    )
    return rework_symbols(quality.evidence)


def _lineage_inputs(snapshots: list[Path], case: dict[str, Any]) -> list[ReworkCheckpointInput]:
    inputs: list[ReworkCheckpointInput] = []
    for index, (snapshot, checkpoint) in enumerate(
        zip(snapshots, case["checkpoints"], strict=True)
    ):
        symbols = _rework_symbols(snapshot, case)
        assert [symbol.model_dump(mode="json") for symbol in symbols] == checkpoint[
            "rework_symbols"
        ]
        removed: tuple[PhysicalLineEvidence, ...] = ()
        added: tuple[PhysicalLineEvidence, ...] = ()
        if index:
            prior = inputs[-1].symbols or ()
            paths = (symbol.path for symbol in (*prior, *symbols))
            removed, added = changed_lines(snapshots[index - 1], snapshot, paths)
        inputs.append(
            ReworkCheckpointInput(
                checkpoint_id=checkpoint["checkpoint"],
                symbols=symbols,
                removed_lines=removed,
                added_lines=added,
            )
        )
    return inputs


def _diff_owners(snapshot: Path, case: dict[str, Any]) -> tuple[SymbolIdentity, ...]:
    production = [i.path for i in build_inventory(snapshot).files if i.role is FileRole.PRODUCTION]
    return production_symbols(measure(snapshot, case["entry_file"])["symbols"], production)


@pytest.mark.parametrize("name", ["synthetic_shop", "forge"])
def test_rework_lineage_ledger_and_regression_attribution_equal_the_fork_live_producers(
    tmp_path: Path, name: str
) -> None:
    case = golden_case(name)
    snapshots = case_snapshots(tmp_path, case)
    inputs = _lineage_inputs(snapshots, case)
    rework = calculate_problem_rework(inputs)
    assert rework.model_dump(mode="json") == case["lineage"]
    compared = 0
    for index in range(1, len(inputs)):
        prior, current = inputs[index - 1], inputs[index]
        key = f"{prior.checkpoint_id}->{current.checkpoint_id}"
        runs = case["runs"].get(current.checkpoint_id)
        if runs is None:
            continue
        inventory = build_inventory(snapshots[index - 1])
        prior_production = [i.path for i in inventory.files if i.role is FileRole.PRODUCTION]
        owners = _diff_owners(snapshots[index], case)
        _, added = changed_lines(
            snapshots[index - 1],
            snapshots[index],
            (*prior_production, *(symbol.path for symbol in owners)),
        )
        attribution = calculate_regression_breadth(
            RegressionBreadthInput(
                transition_id=f"{prior.checkpoint_id}--{current.checkpoint_id}",
                baseline=runs["baseline"],
                produced=runs["produced"],
                changed_symbols=regression_scope(
                    added, owners, rework.transitions[index - 1].lineage_candidates
                ),
            )
        )
        assert without_runs(attribution.model_dump(mode="json")) == case["regressions"][key]
        compared += 1
    assert compared == len(case["regressions"])
