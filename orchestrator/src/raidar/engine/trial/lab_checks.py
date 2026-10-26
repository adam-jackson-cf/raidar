"""The lab's own checks on a step: what Raidar put in place before the agent started.

Every step: the context reset (``raidar.engine.health.setup_script``) and every per-trial
provision (``raidar.engine.trial.provision``). At checkpoint 1 also the seeded workspace
project (``raidar.engine.trial.workspace``), whose ``/app`` digest is checkpoint 1's parent
snapshot. A failed check is the lab's failure, never the agent's: collection makes the step
an infra error.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from raidar.contract.trial import ProbeSummary
from raidar.engine.health.setup_script import reset_check
from raidar.engine.trial.provision import provision_check
from raidar.engine.trial.workspace import SEED_SLUG, seed_check, seeded_digest


def lab_checks(agent_dir: Path, provisions: Sequence[str], *, first_step: bool) -> ProbeSummary:
    """The step's reset, every provision, and at checkpoint 1 the seeded project."""
    reset = reset_check(agent_dir)
    outcomes = [(reset.passed, f"context-reset: {reset.detail}")]
    outcomes += [provision_check(agent_dir, slug) for slug in provisions]
    if first_step and SEED_SLUG in provisions:
        outcomes.append(seed_check(agent_dir))
    failures = tuple(detail for passed, detail in outcomes if not passed)
    return ProbeSummary(passed=not failures, failures=failures)


def seed_parent(agent_dir: Path, provisions: Sequence[str]) -> str | None:
    """Checkpoint 1's parent snapshot: the seeded ``/app`` (``None`` with no seed recorded)."""
    return seeded_digest(agent_dir) if SEED_SLUG in provisions else None
