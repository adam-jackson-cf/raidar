"""Rewrite a converted SCB Harbor task into Raidar's split form.

The converter emits one directory holding everything: hidden tests,
per-step ``test.sh`` and reference solutions. Raidar splits it into:

- ``task/``: what Harbor loads and the agent environment sees. Each step's
  instruction is the pinned upstream ``<checkpoint>.md`` verbatim with only
  the two ``%%%ENTRYPOINT:...%%%`` tokens substituted (``render_spec``), a
  stub ``tests/test.sh`` (Harbor 0.23 requires one for shared-mode steps;
  the Raidar verifier never uses it), the Python pack image with the
  compose ``init: true`` override, and a ``/app`` artifact per step for the
  host-side verifier. No hidden test, test script or solution is present.
- ``oracle/``: the host-only verifier bundle (``raidar.grading.bundle``), graded
  in the pack image so grader and agent run the submission on one image.
- ``references/<step>/``: the converter's canary-stripped reference payload
  per step, used only by ``raidar problem validate``.

Step verification is selected per trial with
``VerifierConfig(import_path=VERIFIER_IMPORT_PATH, kwargs={"oracle_bundle": ...})``.
"""

from __future__ import annotations

import re
import shutil
import stat
from pathlib import Path

import yaml
from harbor.models.task.config import ArtifactConfig, TaskConfig, VerifierEnvironmentMode
from harbor.models.task.task import Task

from raidar.contract.snapshot import app_artifact_config
from raidar.design.problem.models import ProblemId
from raidar.grading.bundle import (
    APP_PLACEHOLDER,
    BundleManifest,
    GraderSpec,
    VerifierBundle,
    build_bundle_from_scb_harbor_task,
    write_bundle,
)
from raidar.grading.sandbox import CONTAINER_LAYOUT
from raidar.subject.environment.python.image import image_tag
from raidar.subject.environment.python.pack_definition import PackSpec
from raidar.subject.environment.python.task import bind_task_environment

VERIFIER_IMPORT_PATH = "raidar.grading.verifier:RaidarVerifier"
AGENT_USER = "agent"
# The reference substitution is factory-x-loops' ``render_prompt``
# (``evaluation/benchmark/scb.py``, FXL commit ac5db0f28364911ef2298e428748120be8b0d56c).
# Raidar differs in one value: ``entry_command`` is the command Raidar's grader
# runs (``uv run --project /app <entry_file>``), not FXL's ``python <entry_file>``.
ENTRY_COMMAND_TOKEN = "%%%ENTRYPOINT:entry_command%%%"
ENTRY_FILE_TOKEN = "%%%ENTRYPOINT:entry_file%%%"
_ENTRYPOINT_ARG = "--entrypoint"
# Frozen task content: these bytes are inside every imported problem's pinned
# ``task_sha256`` (registered 2026-09-30), so they keep that release's wording.
# Changing them is a problem re-release: re-import and re-validate with native parity.
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


def grader_entry_command(bundle: VerifierBundle) -> str | None:
    """The command the grader runs the submission with, as the bundle's steps pass it.

    Every step's ``--entrypoint`` argument, rendered in the container layout
    (``{app}`` -> ``/app``). Steps that disagree are refused: one problem has
    one entry command. ``None`` when no step names one.
    """
    commands: set[str] = set()
    for step in bundle.manifest.steps:
        args = step.pytest_args
        if _ENTRYPOINT_ARG in args and args.index(_ENTRYPOINT_ARG) + 1 < len(args):
            raw = args[args.index(_ENTRYPOINT_ARG) + 1]
            commands.add(raw.replace(APP_PLACEHOLDER, CONTAINER_LAYOUT.app))
    if len(commands) > 1:
        raise RewriteError(f"steps disagree on the entry command: {sorted(commands)}")
    return commands.pop() if commands else None


def render_spec(markdown: str, *, entry_command: str | None, entry_file: str | None) -> str:
    """Upstream checkpoint markdown with only its two entrypoint tokens substituted.

    Nothing else changes: no canary stripping, framing, or other substitutions.
    """
    values = {ENTRY_COMMAND_TOKEN: entry_command, ENTRY_FILE_TOKEN: entry_file}
    for token, value in values.items():
        if token in markdown:
            if value is None:
                raise RewriteError(f"spec uses {token}, which the problem does not define")
            markdown = markdown.replace(token, value)
    return markdown


def problem_entry_file(problem_dir: Path, pack: PackSpec) -> str | None:
    """The problem's ``config.yaml`` ``entry_file`` in the pack's snapshot-file format."""
    config = yaml.safe_load((problem_dir / "config.yaml").read_text())
    raw = config.get("entry_file") if isinstance(config, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise RewriteError(f"{problem_dir.name}/config.yaml: entry_file must be a string")
    try:
        return pack.entry_file(raw)
    except ValueError as exc:
        raise RewriteError(f"{problem_dir.name}/config.yaml: {exc}") from exc


def checkpoint_specs(
    problem_dir: Path, steps: list[str], *, entry_command: str | None, entry_file: str | None
) -> dict[str, str]:
    """Each step's rendered spec, from the pinned ``<problem_dir>/<step>.md``."""
    return {
        name: render_spec(
            (problem_dir / f"{name}.md").read_bytes().decode("utf-8"),
            entry_command=entry_command,
            entry_file=entry_file,
        )
        for name in steps
    }


def render_task(
    converted: Path,
    out: Path,
    *,
    problem: ProblemId,
    steps: list[str],
    pack: PackSpec,
    specs: dict[str, str],
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
        (target / "instruction.md").write_bytes(specs[name].encode("utf-8"))
    Task(out)  # Harbor must load what it will run.


def build_bundle(
    converted: Path,
    out: Path,
    *,
    problem: ProblemId,
    steps: list[str],
    pack: PackSpec,
    source: dict[str, str],
    entry_file: str | None = None,
) -> VerifierBundle:
    """Verifier bundle for the selected steps, graded in the agent's pack image."""
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
                base_image=image_tag(pack), test_dependencies=derived.grader.test_dependencies
            ),
            steps=tuple(staged.step(name) for name in steps),
            entry_file=entry_file,
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
