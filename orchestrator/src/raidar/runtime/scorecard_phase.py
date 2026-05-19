"""Scorecard synthesis phase for one evaluation run."""

from __future__ import annotations

from typing import Any

from raidar.runtime import artifacts as runtime_artifacts
from raidar.runtime import models
from raidar.runtime import scorecard as scorecard_runtime

persist_canonical_verifier_artifacts = runtime_artifacts.persist_canonical_verifier_artifacts
write_run_analysis = runtime_artifacts.write_run_analysis
build_scorecard = scorecard_runtime.build_scorecard
ScorecardBuildContext = models.ScorecardBuildContext


def synthesize_scorecard_phase(
    request: Any,
    phase: Any,
    execution: Any,
    artifacts: Any,
):
    """Score synthesis phase from persisted artifacts and execution outputs."""

    scorecard = build_scorecard(
        ScorecardBuildContext(
            request=request,
            layout=phase.layout,
            context=phase.context,
            artifacts=artifacts,
            execution=execution,
        )
    )
    persist_canonical_verifier_artifacts(phase.layout, scorecard, execution.outputs)
    write_run_analysis(phase.layout, request, scorecard, execution.harbor_result)
    return scorecard
