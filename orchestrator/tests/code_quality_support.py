"""Shared inputs for the code-quality@3 tests: fork goldens, tool env, stored trials.

``fixtures/code_quality/fork_goldens.json.gz`` is produced by running the SCB
fork's own code (``fixtures/code_quality/generate_fork_goldens.py``); it holds
every input beside the fork's outputs. The quality tool environment is the
real ``scb-python-quality`` tool, provisioned on first use into the host
cache (``RAIDAR_TOOL_CACHE`` or the user cache), exactly as scoring uses it.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any

from raidar.scorers.code_quality.measurement import ToolEnvironment, provisioned_environment
from raidar.verifier.models import CaseStatus, ParityVerdict
from raidar.verifier.records import (
    FORK_EVIDENCE_FILE,
    CoverageRef,
    load_step_record,
    step_record_dir,
    write_step_record,
)
from tests.scorer_support import StepFixture, write_files, write_trial

GOLDENS = Path(__file__).parent / "fixtures" / "code_quality" / "fork_goldens.json.gz"


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


def without_executable(payload: dict[str, Any]) -> dict[str, Any]:
    """Raw V/E evidence minus the host path of the evaluator interpreter."""
    interpreter = {**payload["interpreter"], "executable": "<python>"}
    ruff = {**payload["ruff"], "invocation": ["<python>", *payload["ruff"]["invocation"][1:]]}
    return {**payload, "interpreter": interpreter, "ruff": ruff}


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
