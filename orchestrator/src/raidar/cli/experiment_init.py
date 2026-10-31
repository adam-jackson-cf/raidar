"""``raidar init``: start a new experiment.yaml from the two-arm starter.

The starter is the README walkthrough's shape: an ``ab-test`` with one pinned problem, the
current codex agent release, and two arms, ``baseline`` and ``treatment``, both on the default
workflow (``one-shot``) until the author points ``treatment`` at the workflow (or agent)
release under test; then the paired analysis plan. It validates as written. Another variant
of an existing experiment is a new arm inside that experiment, not a new file, so ``init``
refuses a path that already exists. The id follows the ``<runtime>__<subject>__<intent>``
grammar (``NAME2``) and defaults to the directory name, so
``lab/experiments/ab-test/<id>/experiment.yaml`` satisfies ``NAME1``.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
from raidar_contract.releases import ReleaseKind, load_register

from raidar.contract.paths import releases_file

DEFAULT_PROBLEM = "scb/circuit_eval.checkpoint_1@38d627ecf668"
_EXPERIMENT_ID = re.compile(r"^python__[a-z0-9][a-z0-9_.-]*__[a-z0-9][a-z0-9_-]*$")

_STARTER = """\
schema_version: 3
kind: ab-test
id: {experiment_id}
description: Does the treatment arm's workflow change what the agent delivers?

problems:
  - ref: {problem}

runtime: python

agents:
  - id: codex-luna
    release: {agent}          # lab/agents/, registered in lab/releases.yaml
    model: gpt-6-luna
    reasoning: low

arms:
  - id: baseline
    agent: codex-luna
    workflow: {workflow}
  - id: treatment
    agent: codex-luna
    workflow: {workflow}      # point this at the workflow release under test

assessment:
  scorers: [correctness@9, code-quality@8, resource-usage@7]

analysis:
  baseline_arm: baseline
  attempts: 5
  seed: 20260930
  min_pairs: 5               # the lock pins these verdict thresholds
  max_exclusion_fraction: 0.2
  alpha: 0.05
"""


def register(main: click.Group) -> None:
    main.add_command(init_command)


def _experiment_id(experiment: Path, given: str | None) -> str:
    candidate = given or experiment.resolve().parent.name
    if not _EXPERIMENT_ID.fullmatch(candidate):
        raise click.BadParameter(
            f"{candidate!r} is not a valid experiment id: use python__<subject>__<intent> "
            "(lowercase letters, digits, '-', '_')",
            param_hint="--id",
        )
    return candidate


def render_starter(experiment_id: str, problem: str) -> str:
    """The starter experiment.yaml text, on the tree's current codex and one-shot releases."""
    register = load_register(releases_file())
    agent = register.latest(ReleaseKind.AGENT, "codex")
    workflow = register.latest(ReleaseKind.WORKFLOW, "one-shot")
    if agent is None or workflow is None:
        raise click.ClickException("lab/releases.yaml must register codex and one-shot")
    return _STARTER.format(
        experiment_id=experiment_id, problem=problem, agent=agent, workflow=workflow
    )


@click.command("init")
@click.argument("experiment", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--id", "experiment_id", default=None, help="Experiment id (default: the directory).")
@click.option(
    "--problem",
    default=DEFAULT_PROBLEM,
    show_default=True,
    help="Pinned problem reference (<dataset>/<problem>[.<checkpoint>]@<version>).",
)
def init_command(experiment: Path, experiment_id: str | None, problem: str) -> None:
    """Write a new two-arm EXPERIMENT (experiment.yaml)."""
    if experiment.exists():
        raise click.ClickException(
            f"refusing to overwrite {experiment}; to test another variant, add an arm to the "
            "existing experiment.yaml"
        )
    text = render_starter(_experiment_id(experiment, experiment_id), problem)
    experiment.parent.mkdir(parents=True, exist_ok=True)
    experiment.write_text(text, encoding="utf-8")
    click.echo(f"Wrote {experiment}")
    click.echo(f"Next: make experiment-validate EXPERIMENT={experiment}")
