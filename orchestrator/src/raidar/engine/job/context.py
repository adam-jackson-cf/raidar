"""``JobContext``: what a job's trials share (plan, directories, problems, target and its guard)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.contract.job_layout import TRIALS_DIR
from raidar.design.problem.store import ImportedProblem
from raidar.engine.job.plan import ExperimentPlan
from raidar.engine.target.job import JobTarget
from raidar.engine.trial.driver import TrialRunner, run_harbor_trial


@dataclass
class JobContext:
    plan: ExperimentPlan
    job_dir: Path
    job_id: str
    problems: Mapping[int, ImportedProblem]
    target: JobTarget
    runner: TrialRunner = run_harbor_trial
    admission: Mapping[str, Any] | None = None
    """The smoke-before-measure decision (``execution.health.admission``) ``job.json`` records."""

    @property
    def docker_context(self) -> str:
        """The grader target's context: where the verifier's hidden-test grader runs."""
        return self.target.selection.grader.docker_context

    @property
    def trials_dir(self) -> Path:
        return self.job_dir / TRIALS_DIR
