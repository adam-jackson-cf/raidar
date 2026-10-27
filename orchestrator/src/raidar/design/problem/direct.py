"""Direct validation engine: grade reference payloads with the Raidar verifier.

Each (variant, step, repeat) is one ``raidar.grading.grader.grade_step`` call:
a fresh grader sandbox (Docker, ``--network none``) on a fresh copy of the
reference snapshot. This is the grading the verifier performs inside a
Harbor trial, without the agent environment around it.

Declared dependencies follow the canonical grader's offline provisioning rules.
Direct validation does not run the converter's networked ``solve.sh`` installer;
the verifier records which declared packages are provided or unavailable.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from raidar.design.problem.models import ProblemMode
from raidar.design.problem.store import ImportedProblem
from raidar.design.problem.validation import NEGATIVE, POSITIVE, Grade, StepGrades
from raidar.grading.grader import DEFAULT_RUN_TIMEOUT_S, grade_step
from raidar.grading.sandbox import GradingSandbox

DEFAULT_CONCURRENCY = 4


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


def _by_repeat(
    keys: list[GradeKey], grades: list[Grade], repeats: int
) -> dict[str, list[StepGrades]]:
    runs: dict[str, list[dict[str, Grade]]] = {
        variant: [{} for _ in range(repeats)] for variant in (POSITIVE, NEGATIVE)
    }
    for (variant, step, repeat), grade in zip(keys, grades, strict=True):
        runs[variant][repeat][step] = grade
    return {variant: list(run) for variant, run in runs.items()}


async def grade_references(
    problem: ImportedProblem,
    sandbox: GradingSandbox,
    *,
    repeats: int,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout_s: float = DEFAULT_RUN_TIMEOUT_S,
    work_root: Path | None = None,
) -> dict[str, list[StepGrades]]:
    """Oracle grades per variant, one ``{step: Grade}`` per repeat."""
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    gate = asyncio.Semaphore(concurrency)
    with tempfile.TemporaryDirectory(prefix="raidar-problem-validate-", dir=work_root) as tmp:
        work = Path(tmp)
        empty = work / "empty-starter"
        empty.mkdir()
        snapshots = variant_snapshots(problem, empty)

        async def one(variant: str, step: str, repeat: int) -> Grade:
            async with gate:
                result = await grade_step(
                    problem.bundle,
                    step,
                    snapshots[variant][step],
                    sandbox,
                    work / variant / str(repeat) / step,
                    coverage=False,
                    timeout_s=timeout_s,
                )
            return Grade.from_verifier(result.grade)

        keys: list[GradeKey] = [
            (variant, step, repeat)
            for variant in (POSITIVE, NEGATIVE)
            for repeat in range(repeats)
            for step in snapshots[variant]
        ]
        grades = await asyncio.gather(*(one(*key) for key in keys))
    return _by_repeat(keys, list(grades), repeats)
