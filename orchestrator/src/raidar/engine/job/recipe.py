"""Runtime-pack and verifier recipe identities recorded for a job and its admission."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from raidar.design.problem.store import ImportedProblem
from raidar.engine.trial.plan import ExperimentPlan
from raidar.grading.grader_image import grader_image_tag
from raidar.subject.environment import load_runtime_pack
from raidar.subject.environment.python.image import image_lock_hash


def recipe_digests(plan: ExperimentPlan, problems: Mapping[int, ImportedProblem]) -> dict[str, Any]:
    """The pack image recipe hash and the grader recipe digest of every planned problem."""
    graders = {
        str(target.ref): grader_image_tag(problems[target.index].bundle.manifest.grader).rsplit(
            ":", 1
        )[1]
        for target in plan.problems
        if target.index in problems
    }
    return {
        "pack": image_lock_hash(load_runtime_pack(plan.loaded.spec.runtime)),
        "graders": graders,
    }
