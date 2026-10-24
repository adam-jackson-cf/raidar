"""code-quality@3 rejection boundaries: evidence that cannot be trusted never becomes a value.

Mirrors the fork's own boundary contract: a wrong Ruff version, a foreign
diagnostic, a parser failure, a missing or unsuccessful file row, malformed
symbol rows and unreconcilable graphs all raise with the fork's eligibility
code, and the scorer turns them into ``ineligible`` or ``unavailable``
observations with that reason.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from raidar.scoring.code_quality.architecture import (
    ProductionGraphError,
    produce_production_graph,
    strongly_connected_components,
)
from raidar.scoring.code_quality.checkpoint import Gap, eligibility_code, measure_checkpoint
from raidar.scoring.code_quality.measurement import (
    MeasurementError,
    ProcessResult,
    ToolEnvironment,
    run_process,
)
from raidar.scoring.code_quality.quality import ProductionQualityError, produce_production_quality
from raidar.scoring.code_quality.snapshot_tool import cli
from raidar.scoring.code_quality.snapshot_tool.evaluator import (
    EvaluatorInputError,
    evaluate,
)
from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.inventory import FileRole, InventoryFile, build_inventory
from raidar.scoring.framework.result import AssessStatus
from tests.code_quality_support import golden_case, materialise, quality_tools

Runner = Callable[[Sequence[str], Path | None, str | None, float], ProcessResult]


@dataclass
class Scripted:
    """Runs the real tool env except where a rule answers (or raises) instead."""

    rules: list[tuple[Callable[[Sequence[str]], bool], ProcessResult | Exception]] = field(
        default_factory=list
    )

    def __call__(
        self, argv: Sequence[str], cwd: Path | None, stdin: str | None, deadline: float
    ) -> ProcessResult:
        for matches, answer in self.rules:
            if matches(argv):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return run_process(argv, cwd, stdin, deadline)


def _ruff_check(argv: Sequence[str]) -> bool:
    return "check" in argv


def _ruff_version(argv: Sequence[str]) -> bool:
    return "--version" in argv


def _evaluate(argv: Sequence[str]) -> bool:
    return "evaluate" in argv


def _tools(
    *rules: tuple[Callable[[Sequence[str]], bool], ProcessResult | Exception],
) -> ToolEnvironment:
    return ToolEnvironment(python=quality_tools().python, runner=Scripted(list(rules)))


def _ok(stdout: str, returncode: int = 0) -> ProcessResult:
    return ProcessResult(returncode, stdout, "")


def _diagnostic(**overrides: Any) -> str:
    item = {
        "code": "SIM108",
        "filename": "shop/cart.py",
        "location": {"row": 12, "column": 5},
        "end_location": {"row": 15, "column": 19},
        "message": "Use ternary",
    }
    return json.dumps([{**item, **overrides}])


@pytest.fixture(name="shop")
def _shop(tmp_path: Path) -> dict[str, Any]:
    checkpoint = golden_case("synthetic_shop")["checkpoints"][0]
    snapshot = materialise(tmp_path / "snapshot", checkpoint["files"])
    measured = cli.measure(snapshot, "app.py")
    return {
        "snapshot": snapshot,
        "inventory": build_inventory(snapshot).files,
        "files": measured["files"],
        "symbols": measured["symbols"],
        "expected": checkpoint["production"]["production_quality"],
    }


def _produce(shop: dict[str, Any], tools: ToolEnvironment, **overrides: Any) -> Any:
    inputs = {**shop, **overrides}
    return produce_production_quality(
        inputs.get("checkpoint", "checkpoint_1"),
        inputs["snapshot"],
        inputs["inventory"],
        inputs["files"],
        inputs["symbols"],
        tools,
    )


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        ((_ruff_version, _ok("ruff 0.9.0\n")), "measurement_environment_mismatch: Ruff version"),
        ((_ruff_check, _ok("[]", returncode=2)), "canonical Ruff rule unavailable"),
        ((_ruff_check, _ok("not json", returncode=1)), "Ruff emitted malformed JSON"),
        ((_ruff_check, _ok("{}", returncode=1)), "Ruff diagnostics are not a list"),
        ((_ruff_check, _ok(_diagnostic(code="E501"))), "unexpected Ruff diagnostic"),
        ((_ruff_check, _ok(_diagnostic(code=None))), "unexpected Ruff diagnostic"),
        ((_ruff_check, _ok(_diagnostic(location=[12, 5]))), "unexpected Ruff diagnostic"),
        ((_ruff_check, _ok(_diagnostic(filename="other.py"))), "outside production inventory"),
        ((_ruff_check, _ok(_diagnostic(filename="/elsewhere/x.py"))), "outside production"),
        ((_ruff_check, _ok(_diagnostic(location={"row": "x"}))), "malformed Ruff range"),
        (
            (_ruff_check, _ok(_diagnostic(end_location={"row": 12, "column": 5}))),
            "invalid Ruff range",
        ),
        ((_evaluate, _ok('{"error": "bad"}', returncode=2)), "evaluator parser failed"),
        ((_evaluate, _ok("not json")), "evaluator emitted malformed evidence"),
        ((_evaluate, _ok("[]")), "evaluator emitted malformed evidence"),
        ((_evaluate, _ok('{"interpreter": {"version": "3.13.0"}}')), "^measurement_environment"),
        (
            (
                _ruff_check,
                MeasurementError("measurement_environment_mismatch: measurement timed out"),
            ),
            "measurement timed out",
        ),
        (
            (
                _ruff_version,
                MeasurementError("measurement_environment_mismatch: evaluator unavailable"),
            ),
            "Ruff unavailable",
        ),
        (
            (
                _evaluate,
                MeasurementError("measurement_environment_mismatch: evaluator unavailable"),
            ),
            "evaluator unavailable",
        ),
    ],
)
def test_untrusted_measurement_output_is_rejected_with_the_fork_code(
    shop: dict[str, Any], rule: Any, message: str
) -> None:
    with pytest.raises(ProductionQualityError, match=message):
        _produce(shop, _tools(rule))


def test_ruff_syntax_error_diagnostics_are_ignored_like_the_fork(shop: dict[str, Any]) -> None:
    syntax = _diagnostic(code=None, message="SyntaxError: expected ':'")
    result = _produce(shop, _tools((_ruff_check, _ok(syntax, returncode=1))))
    assert result.evidence.ruff.diagnostics == ()
    assert result.verbosity > Decimal(shop["expected"]["verbosity"])


def _symbol_rows(shop: dict[str, Any], **changes: Any) -> list[dict[str, Any]]:
    rows = [dict(row) for row in shop["symbols"]]
    target = next(row for row in rows if row["type"] == "function" and row["name"] == "count")
    target.update(changes)
    return rows


@pytest.mark.parametrize(
    "changes",
    [
        {"complexity": True},
        {"complexity": "abc"},
        {"complexity": -1},
        {"complexity": 1e20},
        {"sloc": 0},
        {"sloc": True},
        {"start": True},
        {"start": 30, "end": 20},
        {"name": ""},
    ],
)
def test_malformed_symbol_rows_make_erosion_evidence_invalid(
    shop: dict[str, Any], changes: dict[str, Any]
) -> None:
    rows = _symbol_rows(shop, **changes)
    with pytest.raises(ProductionQualityError, match="^production_symbol_evidence_invalid$"):
        _produce(shop, quality_tools(), symbols=rows)


def test_duplicate_symbol_identities_are_rejected(shop: dict[str, Any]) -> None:
    rows = _symbol_rows(shop)
    duplicate = next(row for row in rows if row["name"] == "count")
    with pytest.raises(ProductionQualityError, match="production_symbol_evidence_invalid"):
        _produce(shop, quality_tools(), symbols=[*rows, dict(duplicate)])


@pytest.mark.parametrize(
    ("files", "message"),
    [
        (lambda rows: rows[1:], "missing production file row"),
        (lambda rows: [*rows, rows[0]], "duplicate file row"),
        (lambda rows: [{**rows[0], "success": False}, *rows[1:]], "unsuccessful file row"),
    ],
)
def test_production_files_need_exactly_one_successful_row(
    shop: dict[str, Any],
    files: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    message: str,
) -> None:
    rows = sorted(shop["files"], key=lambda row: row["file_path"])
    with pytest.raises(ProductionQualityError, match=message):
        _produce(shop, quality_tools(), files=files(rows))


@pytest.mark.parametrize(
    ("inventory", "message"),
    [
        (
            lambda items: [*items, InventoryFile(path="app.py", role=FileRole.PRODUCTION)],
            "duplicate inventory identity",
        ),
        (
            lambda items: [*items, {"path": "../escape.py", "role": "production"}],
            "invalid lexical file identity",
        ),
        (lambda items: [*items, {1: "x", "role": "production"}], "mapping keys must be strings"),
        (
            lambda items: [*items, InventoryFile(path="gone.py", role=FileRole.PRODUCTION)],
            "production source unreadable",
        ),
    ],
)
def test_inventory_rows_must_be_unique_lexical_and_readable(
    shop: dict[str, Any], inventory: Callable[[list[Any]], list[Any]], message: str
) -> None:
    files = [*shop["files"], {"file_path": "gone.py", "success": True}]
    with pytest.raises(ProductionQualityError, match=message):
        _produce(shop, quality_tools(), inventory=inventory(list(shop["inventory"])), files=files)


def test_a_checkpoint_needs_an_identity(shop: dict[str, Any]) -> None:
    with pytest.raises(ProductionQualityError, match="checkpoint identity missing"):
        _produce(shop, quality_tools(), checkpoint="")


def _graph(nodes: list[tuple[str, str]], edges: list[tuple[str, str, Any]]) -> dict[str, Any]:
    return {
        "nodes": [{"identity": identity, "path": path} for identity, path in nodes],
        "edges": [{"source": s, "target": t, "weight": w} for s, t, w in edges],
    }


_ROLES = [
    {"path": "a.py", "role": "production"},
    {"path": "t.py", "role": "test"},
]


@pytest.mark.parametrize(
    ("inventory", "traversal", "message"),
    [
        ([*_ROLES, {"path": "a.py", "role": "test"}], _graph([], []), "duplicate inventory"),
        ([{"path": "/abs.py", "role": "production"}], _graph([], []), "lexical file identity"),
        ([{"path": "a.py", "role": "vendored"}], _graph([], []), "invalid inventory role"),
        ([{2: "a.py"}], _graph([], []), "mapping keys must be strings"),
        (_ROLES, _graph([("a.py:f", "a.py")], []), "node reconciliation failed"),
        (_ROLES, _graph([("x.py::f", "x.py")], []), "node reconciliation failed"),
        (_ROLES, _graph([("a.py::f", "a.py")], [("a.py::f", "a.py::g", 1)]), "edge reconciliation"),
        (_ROLES, _graph([("a.py::f", "a.py")], [("a.py::f", "a.py::f", True)]), "malformed"),
        (_ROLES, _graph([("a.py::f", "a.py")], [("a.py::f", "a.py::f", 1.5)]), "malformed"),
        (_ROLES, _graph([("a.py::f", "a.py")], [("a.py::f", "a.py::f", -1)]), "invalid graph edge"),
    ],
)
def test_unreconcilable_call_graphs_are_rejected(
    inventory: list[Any], traversal: dict[str, Any], message: str
) -> None:
    with pytest.raises(ProductionGraphError, match=message):
        produce_production_graph("checkpoint_1", inventory, traversal)


def test_architecture_scores_only_production_cycles_and_keeps_cross_role_edges() -> None:
    traversal = _graph(
        [("a.py::f", "a.py"), ("a.py::g", "a.py"), ("a.py::h", "a.py"), ("t.py::t", "t.py")],
        [
            ("a.py::f", "a.py::g", 2),
            ("a.py::g", "a.py::f", 1),
            ("a.py::h", "a.py::h", 1),
            ("a.py::h", "a.py::f", 4),
            ("t.py::t", "a.py::f", 3),
        ],
    )
    result = produce_production_graph("checkpoint_1", _ROLES, traversal)
    assert (result.evidence.cyclic_mass, result.evidence.total_mass) == (Decimal(4), Decimal(8))
    assert result.architecture == Decimal("0.5")
    assert [edge.source for edge in result.evidence.cross_role_edges] == ["t.py::t"]
    with pytest.raises(ProductionGraphError, match="checkpoint identity"):
        produce_production_graph("", _ROLES, traversal)


def test_strongly_connected_components_partition_deep_chains_without_recursion() -> None:
    chain = [f"n{index:05d}" for index in range(5000)]
    edges = {node: [chain[index + 1]] for index, node in enumerate(chain[:-1])}
    edges[chain[-1]] = [chain[0], chain[-1]]
    [component] = strongly_connected_components(chain, edges)
    assert component == tuple(sorted(chain))
    assert strongly_connected_components(["b", "a"], {"a": ["b"]}) == [("b",), ("a",)]


def test_the_evaluator_program_reports_the_fork_source_lines_and_clone_occurrences() -> None:
    checkpoint = golden_case("synthetic_shop")["checkpoints"][0]
    expected = checkpoint["production"]["production_quality"]
    files = [
        {"path": row["path"], "bytes": checkpoint["files"][row["path"]].encode().hex()}
        for row in expected["files"]
    ]
    payload = evaluate(files)
    assert [
        {"path": row["path"], "hash": row["hash"], "source_lines": row["source_lines"]}
        for row in payload["files"]
    ] == expected["files"]
    assert sorted(group["occurrences"] for group in payload["clone_groups"]) == sorted(
        [list(item) for item in group["occurrences"]] for group in expected["clones"]
    )
    assert len(payload["parser_tokenizer_schema_id"]) == 64
    with pytest.raises(EvaluatorInputError):
        evaluate([{"path": "bad.py", "bytes": b"def f(:\n".hex()}])


class _Stream:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.written = ""

    def read(self) -> str:
        return self.text

    def write(self, text: str) -> int:
        self.written += text
        return len(text)


def test_the_tool_cli_reports_parser_failures_usage_and_graphless_measures(tmp_path: Path) -> None:
    bad = json.dumps({"files": [{"path": "bad.py", "bytes": b"def f(:\n".hex()}]})
    out = _Stream()
    assert cli.main(["evaluate"], _Stream(bad), out) == cli.EXIT_INPUT_INVALID  # type: ignore[arg-type]
    assert "error" in json.loads(out.written)
    good = json.dumps({"files": [{"path": "ok.py", "bytes": b"x = 1\n".hex()}]})
    out = _Stream()
    assert cli.main(["evaluate"], _Stream(good), out) == 0  # type: ignore[arg-type]
    assert json.loads(out.written)["files"][0]["source_lines"] == [1]
    snapshot = materialise(tmp_path / "s", {"a.py": "def f():\n    return 1\n"})
    out = _Stream()
    assert cli.main(["measure", str(snapshot)], _Stream(), out) == 0  # type: ignore[arg-type]
    assert json.loads(out.written)["graph"] is None
    assert cli.main(["unknown"], _Stream(), _Stream()) == cli.EXIT_USAGE  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (_ok("boom", returncode=3), "snapshot measurement failed: boom"),
        (_ok("not json"), "emitted malformed rows"),
        (_ok("[]"), "is not an object"),
    ],
)
def test_a_failed_snapshot_measurement_is_a_typed_error(
    tmp_path: Path, answer: ProcessResult, message: str
) -> None:
    tools = ToolEnvironment(python=Path("python"), runner=Scripted([(lambda argv: True, answer)]))
    with pytest.raises(MeasurementError, match=message):
        tools.measure(tmp_path, None)


def test_processes_that_time_out_or_cannot_start_are_environment_mismatches(
    tmp_path: Path,
) -> None:
    with pytest.raises(MeasurementError, match="timed out"):
        run_process(
            [str(quality_tools().python), "-c", "import time; time.sleep(5)"], None, None, 0.2
        )
    with pytest.raises(MeasurementError, match="evaluator unavailable"):
        run_process([str(tmp_path / "missing-python")], None, None, 5)


def _step(snapshot: Path | None, *, reached: bool = True) -> StepInputs:
    return StepInputs(
        step="checkpoint_1",
        reached=reached,
        checkpoint="checkpoint_1",
        snapshot=snapshot,
        snapshot_digest=None,
        record=None,
        record_digest=None,
        trace_dir=None,
        trace_digest=None,
        agent_result=None,
    )


def _gap(step: StepInputs, tools: ToolEnvironment, entry: str | None = None) -> Gap:
    outcome = measure_checkpoint(AssessContext("p", (step,)), step, tools, entry).outcome
    assert isinstance(outcome, Gap)
    return outcome


def test_checkpoint_gaps_carry_the_fork_code_as_their_reason(tmp_path: Path) -> None:
    js = materialise(tmp_path / "js", {"app.py": "x = 1\n", "web/app.js": "let x = 1;\n"})
    unsupported = _gap(_step(js), quality_tools(), "app.py")
    assert unsupported.status is AssessStatus.INELIGIBLE
    assert unsupported.reason.startswith("production_language_unsupported: ")
    assert _gap(_step(None), quality_tools()).status is AssessStatus.UNAVAILABLE
    py = materialise(tmp_path / "py", {"app.py": "def f():\n    return 1\n"})
    error = MeasurementError("measurement_environment_mismatch: x")
    failing = ToolEnvironment(python=Path("python"), runner=Scripted([(lambda argv: True, error)]))
    assert _gap(_step(py), failing).reason == "measurement_environment_mismatch: x"
    assert eligibility_code(ValueError("something odd")) == "score_evidence_invalid"
