"""Direct validation engine: grade reference payloads with the Raidar oracle.

Each (variant, step, repeat) is one ``raidar.oracle.grader.grade_step`` call:
a fresh grader sandbox (Docker, ``--network none``) on a fresh copy of the
reference snapshot. This is the grading the oracle verifier performs inside a
Harbor trial, without the agent environment around it.

The converter's ``solve.sh`` installs ``requirements.txt`` before a reference
runs; direct grading does not, so a reference that declares requirements is
refused here (use the Harbor engine, which runs ``solve.sh``).
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from raidar.oracle.grader import DEFAULT_RUN_TIMEOUT_S, grade_step
from raidar.oracle.sandbox import GradingSandbox
from raidar.problems.models import ProblemMode
from raidar.problems.store import ImportedProblem
from raidar.problems.validation import NEGATIVE, POSITIVE, Grade, StepGrades

DEFAULT_CONCURRENCY = 4


class DirectEngineError(RuntimeError):
    """The problem cannot be validated by direct grading."""


def declared_requirements(snapshot: Path) -> list[str]:
    path = snapshot / "requirements.txt"
    if not path.is_file():
        return []
    lines = (line.strip() for line in path.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def variant_snapshots(problem: ImportedProblem, empty: Path) -> dict[str, dict[str, Path]]:
    """Reference k for step k (positive); reference k-1, or the empty starter (negative)."""
    steps = [step.name for step in problem.manifest.steps]
    positive = {step: problem.reference(step) for step in steps}
    negative: dict[str, Path] = {}
    for index, step in enumerate(steps):
        first = index == 0 or problem.manifest.mode is ProblemMode.ONE_SHOT
        negative[step] = empty if first else problem.reference(steps[index - 1])
    return {POSITIVE: positive, NEGATIVE: negative}


def _refuse_requirements(problem: ImportedProblem) -> None:
    for spec in problem.manifest.steps:
        requirements = declared_requirements(problem.reference(spec.name))
        if requirements:
            raise DirectEngineError(
                f"reference {spec.name} declares requirements {requirements}; "
                "direct grading does not install them"
            )


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
    _refuse_requirements(problem)
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
            return Grade.from_oracle(result.grade)

        keys: list[GradeKey] = [
            (variant, step, repeat)
            for variant in (POSITIVE, NEGATIVE)
            for repeat in range(repeats)
            for step in snapshots[variant]
        ]
        grades = await asyncio.gather(*(one(*key) for key in keys))
    return _by_repeat(keys, list(grades), repeats)
