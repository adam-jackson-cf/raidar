"""The verifier-side halves of ``raidar assess``: regrade, and pre-fix grading before a rescore.

- ``regrade``: the stored step snapshots graded again (``raidar.verifier.regrade``)
  and, with an Assessment store, the stored pre-fix grades beside them
  (``raidar.verifier.prefix_grade``); the report's ``match`` covers both;
- ``grade_pre_fix``: before a rescore whose scorers read the grades of
  guardrail-gate@3's pre-fix snapshots (``PRE_FIX_GRADE_SCORERS``), every
  not-yet-graded snapshot of the target is graded into the store. A replay
  grades nothing (it must reproduce stored bytes only).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import click

from raidar.verifier.bundle import BundleError
from raidar.verifier.prefix_grade import grade_pre_fix_path, regrade_pre_fix_path
from raidar.verifier.regrade import RegradeError, RegradeOptions, regrade_path

# Scorers that read the offline grades of guardrail-gate@3's pre-fix snapshots.
PRE_FIX_GRADE_SCORERS = frozenset({"gate-effort@4"})


def regrade(target: Path, options: RegradeOptions, store: Path | None) -> dict[str, Any]:
    """The regrade report; a regrade that cannot run is a click error."""
    try:
        report = asyncio.run(regrade_path(target, options))
        if store is None:
            return report
        pre_fix = asyncio.run(regrade_pre_fix_path(target, store, options))
    except (RegradeError, BundleError) as exc:
        raise click.ClickException(str(exc)) from exc
    return {**report, "pre_fix": pre_fix, "match": bool(report["match"]) and pre_fix["match"]}


def grade_pre_fix(
    target: Path,
    store: Path,
    scorer_refs: Iterable[str],
    options: RegradeOptions,
    *,
    replay: bool,
) -> dict[str, Any] | None:
    """Grade the target's ungraded pre-fix snapshots into the store, when a scorer reads them."""
    if replay or not PRE_FIX_GRADE_SCORERS.intersection(scorer_refs):
        return None
    try:
        return asyncio.run(grade_pre_fix_path(target, store, options))
    except (RegradeError, BundleError) as exc:
        raise click.ClickException(f"pre-fix grading: {exc}") from exc


__all__ = ["PRE_FIX_GRADE_SCORERS", "RegradeOptions", "grade_pre_fix", "regrade"]
