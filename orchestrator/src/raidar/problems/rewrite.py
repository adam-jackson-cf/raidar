"""Rewrite a converted SCB Harbor task into Raidar's split form.

The converter emits one directory holding everything: instructions, hidden
tests, per-step ``test.sh`` and reference solutions. Raidar splits it into:

- ``task/``: what Harbor loads and the agent environment sees. Instructions,
  a stub ``tests/test.sh`` (Harbor 0.23 requires one for shared-mode steps;
  the Raidar oracle verifier never uploads it), the Python pack image with the
  compose ``init: true`` override, and a ``/app`` artifact per step for the
  host-side oracle. No hidden test, test script or solution is present.
- ``oracle/``: the host-only oracle bundle (``raidar.oracle.bundle``), graded
  in the pack image so grader and agent run the submission on one image.
- ``references/<step>/``: the converter's canary-stripped reference payload
  per step, used only by ``raidar problem validate``.

Step verification is selected per trial with
``VerifierConfig(import_path=ORACLE_VERIFIER_IMPORT_PATH, kwargs={"oracle_bundle": ...})``.
"""

from __future__ import annotations

import re
import shutil
import stat
from pathlib import Path

from harbor.models.task.config import ArtifactConfig, TaskConfig, VerifierEnvironmentMode
from harbor.models.task.task import Task

from raidar.oracle.bundle import (
    BundleManifest,
    GraderSpec,
    OracleBundle,
    build_bundle_from_scb_harbor_task,
    write_bundle,
)
from raidar.oracle.snapshot import app_artifact_config
from raidar.packs.python import PackSpec, bind_task_environment
from raidar.problems.models import ProblemId

ORACLE_VERIFIER_IMPORT_PATH = "raidar.oracle.verifier:RaidarOracleVerifier"
AGENT_USER = "agent"
STUB_TEST_SH = """#!/bin/bash
# Raidar: hidden tests are graded host-side by raidar.oracle.verifier.
# Harbor requires a test script for shared-mode steps; this one is never run.
echo "raidar: the oracle is host-side" >&2
exit 1
"""
# Checkpoint test modules a bundle keeps only when one of its steps selects them.
_SCB_TEST_FILE = re.compile(r"test_checkpoint_\d+\.py")


class RewriteError(ValueError):
    """The converted task cannot be rewritten into the Raidar split."""


def converted_steps(converted: Path) -> list[str]:
    config = TaskConfig.model_validate_toml((converted / "task.toml").read_text())
    return [step.name for step in config.steps or []]


def select_steps(available: list[str], checkpoint: str | None) -> list[str]:
    """All steps, or the first checkpoint alone as a one-shot task.

    A later checkpoint cannot stand alone: its spec and prior-test selection
    assume the earlier checkpoints' behaviour already exists.
    """
    if not available:
        raise RewriteError("converted task declares no [[steps]]")
    if checkpoint is None:
        return list(available)
    if checkpoint != available[0]:
        raise RewriteError(
            f"one-shot import supports the first checkpoint ({available[0]}) only, not {checkpoint}"
        )
    return [checkpoint]


def render_task(
    converted: Path, out: Path, *, problem: ProblemId, steps: list[str], pack: PackSpec
) -> None:
    config = TaskConfig.model_validate_toml((converted / "task.toml").read_text())
    by_name = {step.name: step for step in config.steps or []}
    out.mkdir(parents=True)
    env_fields = bind_task_environment(out / "environment", pack)
    artifact = ArtifactConfig.model_validate(app_artifact_config())
    if config.task is not None:
        config.task.name = problem.name
    config.metadata = {
        **config.metadata,
        "raidar_problem": str(problem),
        "raidar_mode": problem.mode.value,
    }
    config.environment.docker_image = env_fields["docker_image"]
    config.agent.user = AGENT_USER
    config.verifier.environment_mode = VerifierEnvironmentMode.SHARED
    config.artifacts = []
    config.steps = [
        by_name[name].model_copy(update={"artifacts": [artifact], "min_reward": None})
        for name in steps
    ]
    (out / "task.toml").write_text(config.model_dump_toml())
    stub = out / "tests" / "test.sh"
    stub.parent.mkdir()
    stub.write_text(STUB_TEST_SH)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    for name in steps:
        target = out / "steps" / name
        target.mkdir(parents=True)
        shutil.copy2(converted / "steps" / name / "instruction.md", target / "instruction.md")
    Task(out)  # Harbor must load what it will run.


def build_bundle(
    converted: Path,
    out: Path,
    *,
    problem: ProblemId,
    steps: list[str],
    pack_image: str,
    source: dict[str, str],
) -> OracleBundle:
    """Oracle bundle for the selected steps, graded in the agent's pack image."""
    staged = build_bundle_from_scb_harbor_task(
        converted, out.with_name(out.name + ".staged"), source=source
    )
    try:
        derived = staged.manifest
        manifest = BundleManifest(
            problem=str(problem),
            source=derived.source,
            env=derived.env,
            grader=GraderSpec(
                base_image=pack_image, test_dependencies=derived.grader.test_dependencies
            ),
            steps=tuple(staged.step(name) for name in steps),
        )
        selected = {name for step in manifest.steps for name in step.test_files}
        tests = out.with_name(out.name + ".tests")
        shutil.copytree(
            converted / "tests",
            tests,
            ignore=lambda _d, names: {
                n for n in names if _SCB_TEST_FILE.fullmatch(n) and n not in selected
            },
        )
        return write_bundle(out, manifest, tests)
    finally:
        shutil.rmtree(staged.root, ignore_errors=True)
        shutil.rmtree(out.with_name(out.name + ".tests"), ignore_errors=True)


def copy_references(converted: Path, out: Path, steps: list[str]) -> None:
    out.mkdir(parents=True)
    for name in steps:
        shutil.copytree(
            converted / "steps" / name / "solution" / "payload", out / name, symlinks=True
        )
