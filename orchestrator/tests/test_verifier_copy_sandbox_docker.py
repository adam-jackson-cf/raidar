"""DockerCopySandbox grading against a real grader image (a ready Docker target)."""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.design.problem import direct
from raidar.design.problem.models import ProblemId
from raidar.grading.grader_image import ensure_grader_image
from raidar.grading.resolver import UvResolver
from raidar.grading.sandbox import DockerCopySandbox
from tests.docker_trial_support import docker_context
from tests.problems_support import POSITIVE_REF, build_fixture_store
from tests.verifier_support import LocalProcessSandbox

pytestmark = pytest.mark.docker


async def test_copy_sandbox_grades_the_fixture_like_the_local_grader(tmp_path: Path) -> None:
    problem = build_fixture_store(tmp_path / "problems", resolver=UvResolver()).load(
        ProblemId.parse(POSITIVE_REF)
    )
    image = await ensure_grader_image(problem.bundle, docker_context=docker_context())
    local = await direct.grade_references(
        problem, LocalProcessSandbox(tmp_path / "local"), repeats=1, concurrency=1
    )
    copied = await direct.grade_references(
        problem,
        DockerCopySandbox(image=image.tag, docker_context=docker_context()),
        repeats=1,
        concurrency=1,
    )

    assert copied == local
    assert all(
        grade.infra_failure is None for runs in copied.values() for grade in runs[0].values()
    )
