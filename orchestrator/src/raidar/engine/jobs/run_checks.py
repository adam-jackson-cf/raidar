"""The checks ``raidar run`` makes before any trial starts.

Credentials for every selected arm, the planned problems, and smoke-before-measure admission
(``raidar.engine.health.admission``: a non-smoke experiment needs a clean matching smoke job,
or ``--allow-unsmoked``). A refusal is a ``RunRefusedError``; the CLI reports it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from raidar.design.problems.store import ImportedProblem, ProblemStore, ProblemStoreError
from raidar.engine.health.admission import AdmissionError, admit, experiment_kind, identity
from raidar.engine.health.provenance import recipe_digests, runner_provenance
from raidar.engine.jobs.credentials import CredentialPreflightError, preflight_credentials
from raidar.engine.jobs.plan import ExperimentPlan
from raidar.engine.jobs.run import load_problems
from raidar.engine.targets.job import JobTarget


class RunRefusedError(RuntimeError):
    """A run refused before any trial: missing credentials, unusable problems or admission."""


def prepare_run(
    plan: ExperimentPlan,
    store: ProblemStore,
    target: JobTarget,
    jobs_dir: Path,
    *,
    allow_unsmoked: bool,
) -> tuple[dict[int, ImportedProblem], dict[str, Any]]:
    """The planned problems and the admission record; any refusal is a ``RunRefusedError``."""
    try:
        preflight_credentials(plan)
        problems = load_problems(plan, store)
    except (CredentialPreflightError, ProblemStoreError) as error:
        raise RunRefusedError(str(error)) from error
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
        raise RunRefusedError(str(error)) from error
    return problems, admission
