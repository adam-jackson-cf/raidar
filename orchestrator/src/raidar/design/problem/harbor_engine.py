"""Harbor validation engine: the imported task run as a real Harbor trial.

Each repeat of each variant is one Harbor trial of the imported agent-visible
task with Harbor's ``oracle`` agent, which runs a per-step ``solve.sh`` that
installs the variant's payload into ``/app``. Verification is the Raidar verifier
(``VERIFIER_IMPORT_PATH``) with the pre-agent leak guard
installed, exactly as an experiment trial is wired. Grades are read from the
host-only ``raidar-oracle/steps/<step>/record.json`` records, never from
Harbor's aggregated trial reward.

The solutions exist only in this engine's scratch copy of the task; the
imported task itself never carries them.
"""

from __future__ import annotations

import asyncio
import shutil
import stat
from collections.abc import Mapping
from pathlib import Path

from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    TaskConfig,
    TrialConfig,
    VerifierConfig,
)
from harbor.trial.trial import Trial

from raidar.design.problem.rewrite import VERIFIER_IMPORT_PATH
from raidar.design.problem.store import ImportedProblem
from raidar.design.problem.validation import Grade, StepGrades
from raidar.grading.guard import install_leak_guard
from raidar.grading.records import load_step_record, step_record_dir

SOLVE_SH = """#!/bin/bash
# Raidar validation: install this step's payload as the whole /app state.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
find /app -mindepth 1 -delete
cp -a "$DIR/payload/." /app/
cd /app
if [ -f requirements.txt ] && grep -qv '^\\s*\\(#\\|$\\)' requirements.txt; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
"""


def variant_task(problem: ImportedProblem, payloads: Mapping[str, Path], dest: Path) -> Path:
    """Copy the imported task and give each step a solution installing its payload."""
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(problem.task_dir, dest, symlinks=True)
    for step, payload in payloads.items():
        solution = dest / "steps" / step / "solution"
        shutil.copytree(payload, solution / "payload", symlinks=True)
        solve = solution / "solve.sh"
        solve.write_text(SOLVE_SH)
        solve.chmod(solve.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dest


def trial_config(
    task: Path, trials_dir: Path, name: str, bundle_root: Path, *, docker_context: str
) -> TrialConfig:
    return TrialConfig(
        task=TaskConfig(path=task),
        trial_name=name,
        trials_dir=trials_dir,
        agent=AgentConfig(name="oracle"),
        environment=EnvironmentConfig(type="docker", delete=True),
        verifier=VerifierConfig(
            import_path=VERIFIER_IMPORT_PATH,
            kwargs={
                "oracle_bundle": str(bundle_root),
                "docker_context": docker_context,
                "snapshot_source": "artifact",
                "coverage": False,
            },
        ),
    )


def read_grades(
    trial_dir: Path, steps: list[str], trial_exception: str | None = None
) -> dict[str, Grade]:
    """Per-step grades; a step without a verifier record is an infrastructure failure.

    Harbor's ``Trial.run`` records exceptions instead of raising, so an
    environment outage surfaces here as missing records; its exception type is
    kept in the infra reason.
    """
    missing = "no_verifier_record" + (f":{trial_exception}" if trial_exception else "")
    grades: dict[str, Grade] = {}
    for step in steps:
        record_dir = step_record_dir(trial_dir, step)
        if not (record_dir / "record.json").is_file():
            grades[step] = Grade("oracle", step, {}, {}, missing)
            continue
        grades[step] = Grade.from_verifier(load_step_record(record_dir).grade)
    return grades


async def _run_trial(config: TrialConfig, bundle_root: Path) -> tuple[Path, str | None]:
    trial = await Trial.create(config)
    install_leak_guard(trial, bundle_root)
    result = await trial.run()
    error = result.exception_info.exception_type if result.exception_info else None
    return trial.paths.trial_dir, error


async def run_variant(
    problem: ImportedProblem,
    variant: str,
    payloads: Mapping[str, Path],
    work: Path,
    *,
    repeats: int,
    docker_context: str,
) -> list[StepGrades]:
    """Run ``repeats`` concurrent trials of one variant; grades per repeat."""
    task = variant_task(problem, payloads, work / "tasks" / variant)
    bundle_root = problem.bundle.root
    configs = [
        trial_config(
            task, work / "trials", f"{variant}-{index}", bundle_root, docker_context=docker_context
        )
        for index in range(repeats)
    ]
    outcomes = await asyncio.gather(*(_run_trial(config, bundle_root) for config in configs))
    steps = list(payloads)
    return [read_grades(trial_dir, steps, error) for trial_dir, error in outcomes]
