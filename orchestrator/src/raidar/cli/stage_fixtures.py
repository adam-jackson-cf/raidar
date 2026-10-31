"""``raidar stage check``: fixtures offline in the pack container, or a trial's replay.

Fixtures and replays run twice-checked in a throwaway pack container as UID 1000 (never on the
host); a passing check publishes the canary record and the expected installed-tree digest.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import click
from raidar_contract.package.manifest import StagePackage

from raidar.cli.stage_lock import lock_problems
from raidar.cli.stage_package import load_named
from raidar.cli.target_options import docker_selection, target_option
from raidar.contract.paths import stage_checks_root, tmp_root
from raidar.subject.environment import load_runtime_pack
from raidar.subject.environment.python.image import image_lock_hash, image_tag, inspect_image
from raidar.subject.workflow.identity import package_primitives
from raidar.subject.workflow.stage_check import (
    CaseResult,
    captured_states,
    check_package,
    record_json,
    replay_trial,
)

RUNTIME = "python"


def _echo_cases(cases: list[CaseResult]) -> None:
    for case in cases:
        mark = "ok  " if case.ok else "FAIL"
        detail = "; ".join(case.problems)
        click.echo(f"  {mark} {case.kind}/{case.case}" + (f": {detail}" if detail else ""))


def _pack(target_name: str | None) -> tuple[str, str, str]:
    """(image tag, docker context, pack recipe hash) for the python pack on the target."""
    selection = docker_selection(target_name)
    spec = load_runtime_pack(RUNTIME)
    tag = image_tag(spec)
    if inspect_image(tag) is None:
        raise click.ClickException(f"pack image {tag} is absent: run make pack-build")
    return tag, selection.trial.docker_context, image_lock_hash(spec)


def _logs_dir(name: str, purpose: str) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    return tmp_root() / "stage-check" / name.replace("/", "--") / f"{purpose}-{stamp}"


def _publish_installed(path: Path, installed: dict[str, object]) -> None:
    """Merge this architecture's expected installed-tree digest and wheels into ``path``."""
    known = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    known[str(installed["arch"])] = {
        "tree_digest": installed["tree_digest"],
        "wheels": installed["wheels"],
    }
    path.write_text(json.dumps(known, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _replay(name: str, package: StagePackage, trial: Path, image: str, context: str) -> None:
    states = captured_states(trial, name)
    if not states:
        raise click.ClickException(f"{trial} holds no captured state for {name}")
    logs = _logs_dir(name, "replay")
    try:
        runs = asyncio.run(
            replay_trial(package, name, states, image=image, docker_context=context, logs_dir=logs)
        )
    except Exception as error:
        raise click.ClickException(f"stage-check replay could not complete: {error}") from error
    for run in runs:
        outcome = run.status or f"unusable ({run.problem})"
        click.echo(f"  {run.snapshot}: {outcome} stdout={run.stdout_sha256[:12]}")
    if any(run.status is None for run in runs):
        raise click.ClickException("a captured state gave an unusable result (replay disposition)")


@click.command("check")
@click.argument("name")
@click.option(
    "--replay",
    "replay",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="A trial directory: run its captured state through the package instead of fixtures.",
)
@target_option
def check_command(name: str, replay: Path | None, target_name: str | None) -> None:
    """Check NAME's fixtures offline in the pack container, or REPLAY a trial's captured state."""
    root, package = load_named(name)
    problems = lock_problems(root)
    if problems:
        raise click.ClickException("; ".join(problems))
    image, context, pack = _pack(target_name)
    if replay is not None:
        _replay(name, package, replay, image, context)
        return
    logs = _logs_dir(name, "check")
    try:
        report = asyncio.run(
            check_package(
                package,
                name,
                image=image,
                docker_context=context,
                pack=pack,
                primitives=package_primitives([package]),
                logs_dir=logs,
            )
        )
    except Exception as error:
        raise click.ClickException(f"stage-check could not complete: {error}") from error
    for problem in report.static:
        click.echo(f"  FAIL static: {problem}")
    _echo_cases(list(report.cases))
    if not report.ok:
        raise click.ClickException(f"{name} failed its checks (calls recorded under {logs})")
    root_dir = stage_checks_root()
    root_dir.mkdir(parents=True, exist_ok=True)
    if report.installed is not None:
        _publish_installed(root_dir / f"{package.digest}.installed.json", report.installed)
    if report.record is None:
        click.echo(f"{name} passed; it declares no stop, so there is no canary record")
        return
    target = root_dir / f"{package.digest}.json"
    target.write_text(json.dumps(record_json(report.record), indent=2, sort_keys=True) + "\n")
    click.echo(f"{name} passed; canary record {target}")
