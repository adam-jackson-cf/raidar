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

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from raidar.scorers.code_quality import arithmetic
from raidar.scorers.code_quality.architecture import produce_production_graph
from raidar.scorers.code_quality.checkpoint import eligibility_code
from raidar.scorers.code_quality.diffs import (
    changed_lines,
    production_symbols,
    regression_scope,
    rework_symbols,
)
from raidar.scorers.code_quality.lineage import (
    calculate_problem_rework,
    calculate_transition_rework,
)
from raidar.scorers.code_quality.lineage_models import (
    PhysicalLineEvidence,
    RegressionBreadthInput,
    ReworkCheckpointInput,
    SymbolIdentity,
    TransitionReworkEvidence,
)
from raidar.scorers.code_quality.models import CheckpointCorrectnessEvidence
from raidar.scorers.code_quality.quality import ProductionQualityError, produce_production_quality
from raidar.scorers.code_quality.regression import calculate_regression_breadth
from raidar.scorers.code_quality.tool_env.snapshot_tool.cli import measure
from raidar.scorers.inventory import FileRole, build_inventory
from tests.code_quality_support import (
    fork_goldens,
    golden_case,
    materialise,
    quality_tools,
    without_executable,
    without_runs,
)

CASES = ("synthetic_shop", "language_features", "forge")
_SYMBOL_ORDER = ("file_path", "start", "end", "name", "type")


def _snapshots(tmp_path: Path, case: dict[str, Any]) -> list[Path]:
    return [
        materialise(tmp_path / checkpoint["checkpoint"], checkpoint["files"])
        for checkpoint in case["checkpoints"]
    ]


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


@pytest.mark.parametrize("name", CASES)
def test_measurement_adapters_match_the_fork_rows_symbols_and_call_graph(
    tmp_path: Path, name: str
) -> None:
    case = golden_case(name)
    for snapshot, checkpoint in zip(_snapshots(tmp_path, case), case["checkpoints"], strict=True):
        measured = measure(snapshot, case["entry_file"])
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


def test_the_tool_env_subprocess_measures_exactly_what_the_in_process_port_does(
    tmp_path: Path,
) -> None:
    case = golden_case("synthetic_shop")
    snapshot = _snapshots(tmp_path, case)[-1]
    assert quality_tools().measure(snapshot, case["entry_file"]) == measure(
        snapshot, case["entry_file"]
    )


@pytest.mark.parametrize("name", CASES)
def test_verbosity_erosion_and_architecture_evidence_equal_the_fork(
    tmp_path: Path, name: str
) -> None:
    case = golden_case(name)
    for snapshot, checkpoint in zip(_snapshots(tmp_path, case), case["checkpoints"], strict=True):
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
    assert without_executable(quality.evidence.model_dump(mode="json")) == without_executable(
        expected["production_quality"]
    )


def test_a_snapshot_without_production_source_is_ineligible_like_the_fork(
    tmp_path: Path,
) -> None:
    case = golden_case("no_production_code")
    [checkpoint] = case["checkpoints"]
    [snapshot] = _snapshots(tmp_path, case)
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
    snapshots = _snapshots(tmp_path, case)
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


def test_rework_is_unavailable_like_the_fork_when_a_checkpoint_has_no_ve_evidence() -> None:
    case = golden_case("language_features")
    assert [item["rework_symbols"] is None for item in case["checkpoints"]].count(True) >= 1
    assert case["lineage"] is None


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
