"""Shared helpers for reading run scorecard metadata."""

from __future__ import annotations

from .schemas.scorecard import EvalRun


def uncached_input_tokens(run: EvalRun) -> int:
    """Return uncached input tokens recorded in process metadata."""
    process = run.scores.metadata.get("process", {})
    if not isinstance(process, dict):
        return 0
    return int(process.get("uncached_input_tokens", 0) or 0)
