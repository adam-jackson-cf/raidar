"""Base definitions for the code-task scorer family."""

from __future__ import annotations

from typing import ClassVar

from raidar.schemas.scenario import ScorerMetricDefinition
from raidar.scorers.base import BaseScorer
from raidar.scorers.common import metric


class CodeTaskScorer(BaseScorer):
    """Internal interface for language-specific code-task scorers."""

    family: ClassVar[str] = "code-task"

    @classmethod
    def default_metrics(cls) -> tuple[ScorerMetricDefinition, ...]:
        """Return the canonical code-task metric interface."""

        return (
            metric(
                "functional",
                "core",
                0.30,
                evidence="Build, test, and gate execution outcomes for the submitted code.",
                score_derivation="Uses the functional score computed from execution outputs.",
                pass_fail="Passes when source exists and functional execution passed.",
            ),
            metric(
                "code-quality",
                "core",
                0.25,
                evidence="Language-specific lint and static quality findings.",
                score_derivation="Combines language-specific lint and static quality signals.",
                pass_fail="Passes when no blocking lint or static quality findings remain.",
            ),
            metric(
                "test-coverage",
                "core",
                0.20,
                evidence="Coverage measurement and configured threshold.",
                score_derivation="Divides measured coverage by threshold, capped at 1.0.",
                pass_fail="Passes when the coverage output passed its configured threshold.",
            ),
            metric(
                "artifact-checks",
                "artifact-checks",
                0.15,
                evidence="Required source and test artifacts in the workspace.",
                score_derivation=(
                    "Scores required artifact matches and language structure evidence."
                ),
                pass_fail="Passes when required artifacts, source files, and test files exist.",
                config={"required_paths": ["src/**"], "path_match": "glob"},
            ),
            metric(
                "verification-stability",
                "core",
                0.10,
                evidence="Verification gate failure count across the run.",
                score_derivation=(
                    "Uses the verification stability score computed from gate history."
                ),
                pass_fail="Passes when verification stability is greater than zero.",
            ),
        )
