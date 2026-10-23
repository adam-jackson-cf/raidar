"""The checks ``raidar run`` makes before any trial starts, as CLI errors.

Credentials for every selected arm, the planned problems, and smoke-before-measure admission
(``raidar.execution.admission``: a non-smoke experiment needs a clean matching smoke job, or
``--allow-unsmoked``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from raidar.execution.admission import AdmissionError, admit, experiment_kind, identity
from raidar.execution.credentials import CredentialPreflightError, preflight_credentials
from raidar.execution.plan import ExperimentPlan
from raidar.execution.provenance import recipe_digests, runner_provenance
from raidar.execution.run import load_problems
from raidar.problems.store import ImportedProblem, ProblemStore, ProblemStoreError
from raidar.targets.job import JobTarget


def prepare_run(
    plan: ExperimentPlan,
    store: ProblemStore,
    target: JobTarget,
    jobs_dir: Path,
    *,
    allow_unsmoked: bool,
) -> tuple[dict[int, ImportedProblem], dict[str, Any]]:
    """The planned problems and the admission record; any refusal is a ``ClickException``."""
    try:
        preflight_credentials(plan)
        problems = load_problems(plan, store)
    except (CredentialPreflightError, ProblemStoreError) as error:
        raise click.ClickException(str(error)) from error
    wanted = identity(
        plan.lock,
        runner_provenance(),
        recipe_digests(plan, problems),
        target.manifest_fields(plan.concurrency)["execution_target"],
    )
    try:
        admission = admit(
            experiment_kind(plan.lock), wanted, jobs_dir.resolve(), allow_unsmoked=allow_unsmoked
        )
    except AdmissionError as error:
        raise click.ClickException(str(error)) from error
    return problems, admission
