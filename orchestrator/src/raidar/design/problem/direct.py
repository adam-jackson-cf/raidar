"""Direct validation engine: grade reference payloads with the Raidar verifier.

Each (variant, step, repeat) is one ``raidar.grading.grader.grade_step`` call:
a fresh grader sandbox (Docker, ``--network none``) on a fresh copy of the
reference snapshot. This is the grading the verifier performs inside a
Harbor trial, without the agent environment around it.

Each reference is graded with its declared set installed from the problem pin's lock
(``declared-wheels/1``, ``raidar.grading.dependency_pins``); the sandbox stays offline.
Direct validation does not run the converter's networked ``solve.sh`` installer.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

from raidar.design.problem.models import ProblemMode
from raidar.design.problem.store import ImportedProblem
from raidar.design.problem.validation import NEGATIVE, POSITIVE, Grade, StepGrades
from raidar.grading.grader import DEFAULT_RUN_TIMEOUT_S, Oracle, OracleSettings, grade_step
from raidar.grading.grader_image import ensure_grader_image
from raidar.grading.records import grading_execution_digest
from raidar.grading.sandbox import DockerCopySandbox, GradingSandbox

DEFAULT_CONCURRENCY = 4


async def prepare_grader_sandbox(
    problem: ImportedProblem, docker_context: str
) -> tuple[DockerCopySandbox, dict[str, object]]:
    image = await ensure_grader_image(problem.bundle, docker_context=docker_context)
    sandbox = DockerCopySandbox(image=image.tag, docker_context=docker_context)
    return sandbox, {
        "grader_image": image.tag,
        "grader_environment_id": await sandbox.environment_id(),
        "grader_implementation_sha256": grading_execution_digest(),
        "docker_context": docker_context,
    }


def variant_snapshots(problem: ImportedProblem, empty: Path) -> dict[str, dict[str, Path]]:
    """Reference k for step k (positive); reference k-1, or the empty starter (negative)."""
    steps = [step.name for step in problem.manifest.steps]
    positive = {step: problem.reference(step) for step in steps}
    negative: dict[str, Path] = {}
    for index, step in enumerate(steps):
        first = index == 0 or problem.manifest.mode is ProblemMode.ONE_SHOT
        negative[step] = empty if first else problem.reference(steps[index - 1])
    return {POSITIVE: positive, NEGATIVE: negative}


GradeKey = tuple[str, str, int]


def _variant_grade_keys(variant: str, snapshots: dict[str, Path], repeats: int) -> list[GradeKey]:
    return [(variant, step, repeat) for repeat in range(repeats) for step in snapshots]


def _by_repeat(
    keys: list[GradeKey], grades: list[Grade], repeats: int
) -> dict[str, list[StepGrades]]:
    runs: dict[str, list[dict[str, Grade]]] = {
        variant: [{} for _ in range(repeats)] for variant in (POSITIVE, NEGATIVE)
    }
    for (variant, step, repeat), grade in zip(keys, grades, strict=True):
        runs[variant][repeat][step] = grade
    return {variant: list(run) for variant, run in runs.items()}


@dataclass(frozen=True)
class ReferenceRuns:
    """How often, how concurrently and how long the reference variants are graded."""

    repeats: int
    concurrency: int = DEFAULT_CONCURRENCY
    timeout_s: float = DEFAULT_RUN_TIMEOUT_S


async def grade_references(
    problem: ImportedProblem,
    sandbox: GradingSandbox,
    runs: ReferenceRuns,
    *,
    work_root: Path | None = None,
) -> dict[str, list[StepGrades]]:
    """Oracle grades per variant, one ``{step: Grade}`` per repeat."""
    repeats, timeout_s = runs.repeats, runs.timeout_s
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    gate = asyncio.Semaphore(runs.concurrency)
    with tempfile.TemporaryDirectory(prefix="raidar-problem-validate-", dir=work_root) as tmp:
        work = Path(tmp)
        empty = work / "empty-starter"
        empty.mkdir()
        snapshots = variant_snapshots(problem, empty)

        async def one(variant: str, step: str, repeat: int) -> Grade:
            async with gate:
                result = await grade_step(
                    Oracle(problem.bundle, sandbox),
                    step,
                    snapshots[variant][step],
                    work / variant / str(repeat) / step,
                    OracleSettings(coverage=False, timeout_s=timeout_s),
                )
            return Grade.from_verifier(result.grade)

        keys: list[GradeKey] = []
        for variant in (POSITIVE, NEGATIVE):
            keys.extend(_variant_grade_keys(variant, snapshots[variant], repeats))
        grades = await asyncio.gather(*(one(*key) for key in keys))
    return _by_repeat(keys, list(grades), repeats)
