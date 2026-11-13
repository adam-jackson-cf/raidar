"""Shared inputs for the code-quality@9 tests: fork goldens, tool env, stored trials.

``fixtures/code_quality/fork_goldens.json.gz`` is produced by running the SCB
fork's own code (``fixtures/code_quality/generate_fork_goldens.py``); it holds
every input beside the fork's outputs. The quality tool environment is the
real ``scb-python-quality`` tool, provisioned on first use into the host
cache (``RAIDAR_TOOL_CACHE`` or ``var/cache/tools``), exactly as scoring uses it.
"""

from __future__ import annotations

import copy
import gzip
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from raidar.contract import file_io
from raidar.grading.models import CaseStatus, ParityVerdict
from raidar.grading.records import (
    FORK_EVIDENCE_FILE,
    CoverageRef,
    load_step_record,
    step_record_dir,
    write_step_record,
)
from raidar.scoring.code_quality.checkpoint import Gap
from raidar.scoring.code_quality.contract import EXPECTED_INTERPRETER, EXPECTED_RUFF_VERSION
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
    RegressionRunEvidence,
    ReworkCheckpointInput,
)
from raidar.scoring.code_quality.measurement import (
    ProcessResult,
    ToolEnvironment,
    provisioned_environment,
)
from raidar.scoring.code_quality.quality import produce_production_quality
from raidar.scoring.code_quality.regression import PARITY_UNAVAILABLE, calculate_regression_breadth
from raidar.scoring.code_quality.snapshot_tool import cli
from raidar.scoring.code_quality.transition import RunEvidences
from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.inventory import FileRole, build_inventory
from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.framework.trial_inputs import load_trial_context
from tests.scorer_support import StepFixture, write_files, write_trial

GOLDENS = Path(__file__).parent / "fixtures" / "code_quality" / "fork_goldens.json.gz"
SHOP_CONFIG = {"entry_file": "app.py"}


@cache
def fork_goldens() -> dict[str, Any]:
    with gzip.open(GOLDENS, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def golden_case(name: str) -> dict[str, Any]:
    return next(case for case in fork_goldens()["cases"] if case["name"] == name)


@cache
def quality_tools() -> ToolEnvironment:
    """The real pinned tool env (provisioned once per cache, then reused)."""
    return provisioned_environment()


def materialise(root: Path, files: Mapping[str, str | bytes]) -> Path:
    write_files(root, dict(files))
    return root


def case_snapshots(tmp_path: Path, case: dict[str, Any]) -> list[Path]:
    return [
        materialise(tmp_path / checkpoint["checkpoint"], checkpoint["files"])
        for checkpoint in case["checkpoints"]
    ]


class _Text:
    """A text stream for ``cli.main``: read-only input or collecting output."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.written = ""

    def read(self) -> str:
        return self.text

    def write(self, text: str) -> int:
        self.written += text
        return len(text)


def _tool_answer(argv: Sequence[str], stdin: str | None) -> ProcessResult:
    """What the pinned tool env prints for ``argv``, computed in this process.

    ``evaluate``/``measure`` run the snapshot tool's own entry point; the interpreter identity
    is the contract's (a host Python is not the pinned evaluator); Ruff finds nothing.
    """
    if "--version" in argv:
        return ProcessResult(0, f"ruff {EXPECTED_RUFF_VERSION}\n", "")
    if "check" in argv:
        return ProcessResult(0, "[]", "")
    start = argv.index("evaluate") if "evaluate" in argv else argv.index("measure")
    command = list(argv[start:])
    out = _Text()
    code = cli.main(command, file_io, _Text(stdin or ""), out)  # type: ignore[arg-type]
    if code == 0 and command[0] == "evaluate":
        payload = json.loads(out.written)
        # The host's identity (binary, stdlib digests) with the contract's language level.
        payload["interpreter"] |= {
            **EXPECTED_INTERPRETER,
            "version": f"{EXPECTED_INTERPRETER['version']}.0",
        }
        return ProcessResult(0, json.dumps(payload), "")
    return ProcessResult(code, out.written, "")


@dataclass
class Scripted:
    """The tool env's processes answered in memory, except where a rule answers (or raises)."""

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
        return _tool_answer(argv, stdin)


def in_memory_tools() -> ToolEnvironment:
    """A tool env whose every process is answered in memory (no real interpreter or Ruff)."""
    return ToolEnvironment(python=Path("python"), runner=Scripted())


def scripted_tools(
    *rules: tuple[Callable[[Sequence[str]], bool], ProcessResult | Exception],
) -> ToolEnvironment:
    return ToolEnvironment(python=Path("python"), runner=Scripted(list(rules)))


def ok_result(stdout: str, returncode: int = 0) -> ProcessResult:
    return ProcessResult(returncode, stdout, "")


FLAKY = "tests/test_cart.py::test_parse[a]"
FLIPPED = ParityVerdict(eligible=False, reasons=("ledger_mismatch", "outcome_mismatch"))


def flip_run(run: dict[str, Any], node: str, outcome: str = "failed") -> dict[str, Any]:
    """``run`` with ``node``'s outcome (and ledger row) set to ``outcome``."""
    flipped = copy.deepcopy(run)
    for key in ("outcomes", "ledger"):
        for item in flipped[key]:
            if item["node_id"] == node:
                item["outcome"] = outcome
    return flipped


def without_executable(payload: dict[str, Any]) -> dict[str, Any]:
    """Raw V/E evidence minus the host's evaluator interpreter identity.

    The interpreter's binary, version and stdlib-file digests and the parser/tokenizer
    schema id (which hashes ``sys.version``) identify the machine that ran the
    evaluator, not the port's values, so goldens recorded on one host must not pin
    them; only the language level (``cache_tag``) is compared.
    """
    interpreter = {"cache_tag": payload["interpreter"]["cache_tag"]}
    ruff = {**payload["ruff"], "invocation": ["<python>", *payload["ruff"]["invocation"][1:]]}
    return {
        **payload,
        "interpreter": interpreter,
        "parser_tokenizer_schema_id": "<host>",
        "ruff": ruff,
    }


def without_runs(attribution: dict[str, Any]) -> dict[str, Any]:
    return {
        **attribution,
        "input": {
            key: value
            for key, value in attribution["input"].items()
            if key not in {"baseline", "produced"}
        },
    }


def case_trial(
    trial_dir: Path,
    case: Mapping[str, Any],
    *,
    coverage: bool = True,
    parity: ParityVerdict | None = None,
    planned: Sequence[str] = (),
    reached: int | None = None,
) -> Path:
    """A stored trial whose steps are a golden case's snapshots, with G run evidence.

    Steps with fork run evidence get a coverage-enabled record (parity as
    given, eligible by default) and the verifier's ``g-kernel-input.json``.
    """
    checkpoints = list(case["checkpoints"])[:reached]
    write_trial(
        trial_dir,
        [
            StepFixture(checkpoint["checkpoint"], checkpoint["files"], (CaseStatus.PASSED,))
            for checkpoint in checkpoints
        ],
        planned=planned,
    )
    for checkpoint in checkpoints:
        name = checkpoint["checkpoint"]
        runs = case["runs"].get(name)
        record_dir = step_record_dir(trial_dir, name)
        record = load_step_record(record_dir)
        verdict = parity if parity is not None else ParityVerdict(eligible=True)
        write_step_record(
            record_dir,
            record.model_copy(update={"coverage": CoverageRef(enabled=coverage, parity=verdict)}),
        )
        if runs is not None:
            (record_dir / FORK_EVIDENCE_FILE).write_text(
                json.dumps({"transition_step": name, **runs}, sort_keys=True)
            )
    return trial_dir


def trial_context(trial: Path, config: dict[str, Any] | None = None) -> AssessContext:
    ctx = load_trial_context(trial)
    ctx.config = dict(SHOP_CONFIG if config is None else config)
    return ctx


def memory_step(name: str, snapshot: Path | None, *, reached: bool = True) -> StepInputs:
    """A step read from memory: a snapshot directory and nothing the oracle recorded."""
    return StepInputs(
        step=name,
        reached=reached,
        checkpoint=name,
        snapshot=snapshot if reached else None,
        snapshot_digest=None,
        record=None,
        record_digest=None,
        trace_dir=None,
        trace_digest=None,
        agent_result=None,
    )


def memory_context(
    tmp_path: Path,
    case: Mapping[str, Any],
    *,
    reached: int | None = None,
    planned: bool = False,
    files: Mapping[str, Mapping[str, str | bytes]] | None = None,
    config: dict[str, Any] | None = None,
) -> AssessContext:
    """A golden case's steps as an in-memory context: snapshots on disk, no stored trial.

    ``files`` replaces a step's snapshot files by checkpoint name. Steps from ``reached`` on
    are left out, or listed as unreached when ``planned``.
    """
    steps = []
    for index, checkpoint in enumerate(case["checkpoints"]):
        name = checkpoint["checkpoint"]
        if reached is None or index < reached:
            files_of = (files or {}).get(name, checkpoint["files"])
            steps.append(memory_step(name, materialise(tmp_path / name, files_of)))
        elif planned:
            steps.append(memory_step(name, None, reached=False))
    ctx = AssessContext(case["name"], tuple(steps))
    ctx.config = dict(SHOP_CONFIG if config is None else config)
    return ctx


def golden_runs(case: Mapping[str, Any]) -> Callable[[StepInputs], RunEvidences | Gap]:
    """G's run evidence for a step as the fork recorded it; a step without any is a typed gap."""

    def runs_for(step: StepInputs) -> RunEvidences | Gap:
        runs = case["runs"].get(step.step)
        if runs is None:
            return Gap(
                AssessStatus.INELIGIBLE, f"{PARITY_UNAVAILABLE}: {FORK_EVIDENCE_FILE} missing"
            )
        return (
            RegressionRunEvidence.model_validate(runs["baseline"]),
            RegressionRunEvidence.model_validate(runs["produced"]),
        )

    return runs_for


def _production_paths(snapshot: Path) -> list[str]:
    inventory = build_inventory(snapshot)
    return [item.path for item in inventory.files if item.role is FileRole.PRODUCTION]


def _rework_symbols(snapshot: Path, case: Mapping[str, Any], tools: ToolEnvironment) -> Any:
    """R's identities as the fork's live producer projects them: V/E's eligible symbols."""
    measured = cli.measure(snapshot, case["entry_file"], file_io)
    quality = produce_production_quality(
        "checkpoint",
        snapshot,
        build_inventory(snapshot).files,
        measured["files"],
        measured["symbols"],
        tools,
    )
    return rework_symbols(quality.evidence)


def _lineage_inputs(
    snapshots: list[Path], case: Mapping[str, Any], tools: ToolEnvironment
) -> list[ReworkCheckpointInput]:
    inputs: list[ReworkCheckpointInput] = []
    for index, (snapshot, checkpoint) in enumerate(
        zip(snapshots, case["checkpoints"], strict=True)
    ):
        symbols = _rework_symbols(snapshot, case, tools)
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


def assert_lineage_and_regression_equal_the_fork(
    tmp_path: Path, case: Mapping[str, Any], tools: ToolEnvironment
) -> None:
    """R's ledger and G's attribution over a case's snapshots equal the fork's live producers.

    V/E's eligible symbols do not depend on Ruff, so any tool environment that measures
    like the pinned one gives the same R and G.
    """
    snapshots = case_snapshots(tmp_path, dict(case))
    inputs = _lineage_inputs(snapshots, case, tools)
    rework = calculate_problem_rework(inputs)
    assert rework.model_dump(mode="json") == case["lineage"]
    compared = 0
    for index in range(1, len(inputs)):
        prior, current = inputs[index - 1], inputs[index]
        key = f"{prior.checkpoint_id}->{current.checkpoint_id}"
        runs = case["runs"].get(current.checkpoint_id)
        if runs is None:
            continue
        prior_production = _production_paths(snapshots[index - 1])
        production = _production_paths(snapshots[index])
        owners = production_symbols(
            cli.measure(snapshots[index], case["entry_file"], file_io)["symbols"], production
        )
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
