"""The verifier-side halves of ``raidar assess``: regrade, and pre-fix grading before a rescore.

- ``regrade``: the stored step snapshots graded again (``raidar.verifier.regrade``)
  and, with an Assessment store, the stored pre-fix grades beside them
  (``raidar.verifier.prefix_grade``); the report's ``match`` covers both;
- ``grade_pre_fix``: before a rescore whose scorers read the grades of
  guardrail-gate@3's pre-fix snapshots (``PRE_FIX_GRADE_SCORERS``), every
  not-yet-graded snapshot of the target is graded into the store. A replay
  grades nothing (it must reproduce stored bytes only);
- ``produce_coverage``: before a rescore whose scorers read offline coverage runs
  (``OFFLINE_COVERAGE_SCORERS``), the coverage run of every step whose trial
  deferred it (``protocol.verifier_coverage: offline``) is made into the store
  (``raidar.verifier.coverage_offline``). A replay makes nothing;
- ``check_coverage`` (``--regrade --coverage-check``): every step's coverage run
  made offline and compared with the trial's own coverage pass or the stored
  offline run; nothing is written.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import click

from raidar.verifier.bundle import BundleError
from raidar.verifier.coverage_offline import check_coverage_path, produce_coverage_path
from raidar.verifier.prefix_grade import grade_pre_fix_path, regrade_pre_fix_path
from raidar.verifier.regrade import RegradeError, RegradeOptions, regrade_path

# Scorers that read the offline grades of guardrail-gate@3's pre-fix snapshots.
PRE_FIX_GRADE_SCORERS = frozenset({"gate-effort@4", "gate-effort@5", "gate-effort@6"})
# Scorers that read the offline coverage runs of steps whose trial deferred the coverage pass.
OFFLINE_COVERAGE_SCORERS = frozenset({"code-quality@2"})


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


def produce_coverage(
    target: Path,
    store: Path,
    scorer_refs: Iterable[str],
    options: RegradeOptions,
    *,
    replay: bool,
) -> dict[str, Any] | None:
    """Make the target's deferred coverage runs into the store, when a scorer reads them."""
    if replay or not OFFLINE_COVERAGE_SCORERS.intersection(scorer_refs):
        return None
    try:
        return asyncio.run(produce_coverage_path(target, store, options))
    except (RegradeError, BundleError) as exc:
        raise click.ClickException(f"offline coverage: {exc}") from exc


def check_coverage(target: Path, options: RegradeOptions, store: Path | None) -> dict[str, Any]:
    """The offline coverage check report; a check that cannot run is a click error."""
    try:
        return asyncio.run(check_coverage_path(target, store, options))
    except (RegradeError, BundleError) as exc:
        raise click.ClickException(str(exc)) from exc


__all__ = [
    "OFFLINE_COVERAGE_SCORERS",
    "PRE_FIX_GRADE_SCORERS",
    "RegradeOptions",
    "check_coverage",
    "grade_pre_fix",
    "produce_coverage",
    "regrade",
]
