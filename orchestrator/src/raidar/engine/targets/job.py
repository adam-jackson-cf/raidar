"""What a job carries about its execution target: selection, daemon facts, and the guard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from raidar.engine.targets.config import Selection
from raidar.engine.targets.guard import TargetGuard
from raidar.engine.targets.health import DockerFacts

MECHANICS_EPOCH = "mechanics-v1"


@dataclass(frozen=True)
class JobTarget:
    """The trial and grader targets of one job; ``guard`` is None where Docker is not used."""

    selection: Selection
    facts: DockerFacts | None = None
    guard: TargetGuard | None = None

    def manifest_fields(self, requested_concurrency: int) -> dict[str, Any]:
        """``job.json`` provenance: where the job ran, and under which mechanics epoch."""
        trial = self.selection.trial
        return {
            "execution_target": {
                "name": trial.name,
                "kind": trial.kind,
                "docker_context": trial.docker_context,
                "trusted": trial.trusted,
                "transport": trial.transport.host if trial.transport else None,
                "max_concurrency": trial.max_concurrency,
                "effective_concurrency": trial.effective_concurrency(requested_concurrency),
                "docker": self.facts.as_record() if self.facts else None,
                "grader": {
                    "name": self.selection.grader.name,
                    "docker_context": self.selection.grader.docker_context,
                },
            },
            "mechanics_epoch": MECHANICS_EPOCH,
        }
