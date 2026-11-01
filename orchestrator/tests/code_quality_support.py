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

from raidar.grading.models import CaseStatus, ParityVerdict
from raidar.grading.records import (
    FORK_EVIDENCE_FILE,
    CoverageRef,
    load_step_record,
    step_record_dir,
    write_step_record,
)
from raidar.scoring.code_quality.measurement import (
    ProcessResult,
    ToolEnvironment,
    provisioned_environment,
    run_process,
)
from raidar.scoring.framework.context import AssessContext
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


def materialise(root: Path, files: Mapping[str, str]) -> Path:
    write_files(root, dict(files))
    return root


FORK_CASES = ("synthetic_shop", "language_features", "forge")


def case_snapshots(tmp_path: Path, case: dict[str, Any]) -> list[Path]:
    return [
        materialise(tmp_path / checkpoint["checkpoint"], checkpoint["files"])
        for checkpoint in case["checkpoints"]
    ]


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
