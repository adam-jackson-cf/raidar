"""Raidar verifier: a host-side Harbor ``BaseVerifier`` that adapts ``StepVerifier``.

Loaded per trial through ``TrialConfig.verifier``::

    VerifierConfig(
        import_path="raidar.grading.verifier:RaidarVerifier",
        kwargs={"oracle_bundle": "<host bundle dir>", ...},
    )

``raidar.grading.step`` does the grading; this module only takes Harbor's construction
arguments and wraps the step's rewards in Harbor's ``VerifierResult``.
"""

from __future__ import annotations

from typing import Any

from harbor.environments.base import BaseEnvironment
from harbor.models.task.task import Task
from harbor.models.trial.paths import TrialPaths
from harbor.models.verifier.result import VerifierResult
from harbor.verifier.base import BaseVerifier

from raidar.grading.step import StepVerifier, VerifierSettings


class RaidarVerifier(BaseVerifier):
    """Harbor's verifier entry point: adapts Harbor's construction to ``StepVerifier``."""

    def __init__(
        self,
        *,
        task: Task,
        trial_paths: TrialPaths,
        environment: BaseEnvironment,
        step_name: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Take ``VerifierSettings`` from ``kwargs``; Harbor's ``BaseVerifier`` takes the rest."""
        settings = VerifierSettings.take(kwargs)
        super().__init__(
            task=task,
            trial_paths=trial_paths,
            environment=environment,
            step_name=step_name,
            **kwargs,
        )
        self.step = StepVerifier(
            settings, trial_paths=trial_paths, environment=environment, step_name=step_name
        )

    async def verify(self) -> VerifierResult:
        return VerifierResult(rewards=await self.step.verify())
