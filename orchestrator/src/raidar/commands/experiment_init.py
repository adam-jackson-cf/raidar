"""``raidar init``: start a new experiment.yaml from the two-arm starter.

The starter is the README walkthrough's shape: an ``ab-test`` with one pinned
problem, one agent on the pack's pinned version, a ``baseline`` condition and a ``rules``
condition (``rules-file@1`` with a stub ``components/AGENTS.md``), and the
paired analysis plan. It validates as written; the author then edits the
problems, conditions and analysis plan. Another variant of an existing
experiment's treatment is a new condition and arm inside that experiment, not
a new file, so ``init`` refuses a path that already exists. The id follows the
``<runtime>__<subject>__<intent>`` grammar (``NAME2``) and defaults to the
directory name, so ``experiments/ab-test/<id>/experiment.yaml`` satisfies ``NAME1``.
"""

from __future__ import annotations

import re
from pathlib import Path

import click

from raidar.agents.config import AgentName, agent_cli
from raidar.packs import load_runtime_pack

DEFAULT_PROBLEM = "scb/circuit_eval.checkpoint_1@38d627ecf668"
RULES_FILE = Path("components") / "AGENTS.md"
_EXPERIMENT_ID = re.compile(r"^python__[a-z0-9][a-z0-9_.-]*__[a-z0-9][a-z0-9_-]*$")

_STARTER = """\
schema_version: 2
kind: ab-test
id: {experiment_id}
description: Does an AGENTS.md rules file change what the agent delivers?

problems:
  - ref: {problem}

runtime: python

agents:
  - id: codex-luna
    name: codex
    provider: openai
    model: gpt-6-luna
    reasoning_effort: low
    version: {codex_version}          # must equal the pack pin

factors: [condition]

conditions:
  - id: baseline
    components: []
  - id: rules
    components:
      - use: rules-file@1
        files: [{rules_file}]

arms:
  - id: baseline
    condition: baseline
    agent: codex-luna
  - id: rules
    condition: rules
    agent: codex-luna

assessment:
  scorers: [correctness@1, code-quality@1, resource-usage@1]

analysis:
  baseline_arm: baseline
  attempts: 5
  seed: 20260930
  min_pairs: 5               # the lock pins these verdict thresholds
  max_exclusion_fraction: 0.2
  alpha: 0.05
"""

_RULES_STUB = """\
# Working rules

Replace this file with the rules the `rules` arm's agent should follow.
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
    """The starter experiment.yaml text, pinned to the Python pack's codex version."""
    codex = load_runtime_pack("python").image.harnesses[agent_cli(AgentName.CODEX).value]
    return _STARTER.format(
        experiment_id=experiment_id,
        problem=problem,
        codex_version=codex.version,
        rules_file=RULES_FILE.as_posix(),
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
    """Write a new two-arm EXPERIMENT (experiment.yaml) and its stub rules file."""
    rules = experiment.parent / RULES_FILE
    existing = [path for path in (experiment, rules) if path.exists()]
    if existing:
        raise click.ClickException(
            f"refusing to overwrite {', '.join(str(path) for path in existing)}; to test another "
            "variant, add a condition and an arm to the existing experiment.yaml"
        )
    text = render_starter(_experiment_id(experiment, experiment_id), problem)
    rules.parent.mkdir(parents=True, exist_ok=True)
    experiment.write_text(text, encoding="utf-8")
    rules.write_text(_RULES_STUB, encoding="utf-8")
    click.echo(f"Wrote {experiment}")
    click.echo(f"Wrote {rules}")
    click.echo(f"Next: make experiment-validate EXPERIMENT={experiment}")
