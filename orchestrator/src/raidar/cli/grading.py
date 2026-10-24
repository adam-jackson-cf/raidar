"""CLI-side wrappers of the verifier's offline grading (``raidar.grading.offline_grading``).

The verifier raises ``OfflineGradingError`` for a grading run that cannot proceed; here every
failure becomes a command error. ``grading_options`` resolves the selected target's grader
target (trusted with hidden tests, ensured), which only the CLI does.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import click

from raidar.cli.target_options import docker_selection
from raidar.grading import offline_grading
from raidar.grading.offline_grading import OfflineGradingError, reads_docker_grades
from raidar.grading.regrade import RegradeOptions


def regrade(target: Path, options: RegradeOptions, store: Path | None) -> dict[str, Any]:
    """The regrade report; a regrade that cannot run is a click error."""
    try:
        return offline_grading.regrade(target, options, store)
    except OfflineGradingError as exc:
        raise click.ClickException(str(exc)) from exc


def grade_pre_fix(
    target: Path,
    store: Path,
    scorer_refs: Iterable[str],
    options: RegradeOptions,
    *,
    replay: bool,
    concurrency: int = 1,
) -> dict[str, Any] | None:
    """Grade the target's ungraded pre-fix snapshots into the store, when a scorer reads them."""
    try:
        return offline_grading.grade_pre_fix(
            target, store, scorer_refs, options, replay=replay, concurrency=concurrency
        )
    except OfflineGradingError as exc:
        raise click.ClickException(str(exc)) from exc


def produce_coverage(
    target: Path,
    store: Path,
    scorer_refs: Iterable[str],
    options: RegradeOptions,
    *,
    replay: bool,
    concurrency: int = 1,
) -> dict[str, Any] | None:
    """Make the target's deferred coverage runs into the store, when a scorer reads them."""
    try:
        return offline_grading.produce_coverage(
            target, store, scorer_refs, options, replay=replay, concurrency=concurrency
        )
    except OfflineGradingError as exc:
        raise click.ClickException(str(exc)) from exc


def check_coverage(
    target: Path, options: RegradeOptions, store: Path | None, concurrency: int = 1
) -> dict[str, Any]:
    """The offline coverage check report; a check that cannot run is a click error."""
    try:
        return offline_grading.check_coverage(target, options, store, concurrency)
    except OfflineGradingError as exc:
        raise click.ClickException(str(exc)) from exc


def grading_options(target_name: str | None, bundle_override: Path | None) -> RegradeOptions:
    """Options on the selected target's grader target (trusted with hidden tests, ensured)."""
    selection = docker_selection(target_name, grader_purpose="hidden-test grading")
    return RegradeOptions(
        docker_context=selection.grader.docker_context, bundle_override=bundle_override
    )


__all__ = [
    "RegradeOptions",
    "check_coverage",
    "grade_pre_fix",
    "grading_options",
    "produce_coverage",
    "reads_docker_grades",
    "regrade",
]
