"""Scenario-epoch experiments for the synthetic review fixture (``<dest>/experiments``).

The default review-surface suite renders both epochs from one fixture, so the
old epoch keeps an observable regression net:

- the scenario-epoch ``skill-benchmark-coding-test`` experiments (three
  revisions, two agent specs) from ``raidar.synthetic``, persisted with the
  scenario-epoch producer. Their run ids do not collide with the experiment
  fixture's;
- one of them relabelled onto the experiment fixture's one-shot problem, so a
  compared family mixes the scenario-epoch composite with
  ``scb-canonical-oneshot@1`` and the headline-policy mismatch warning renders.
  Relabelling rewrites the scenario name and run ids in the persisted
  projection; it is a synthetic comparison, labelled as such like every
  fixture payload.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from raidar.review_projection.legacy import EXPERIMENT_PROJECTION_FILENAME
from raidar.schemas.review import ExperimentReviewProjection
from raidar.synthetic import generate_synthetic_benchmark

LEGACY_SCENARIO = "skill-benchmark-coding-test"
_RELABELLED = f"__{LEGACY_SCENARIO}__v001__codex-cli__gpt-5.5-low"
_RUN_PREFIX = "synthetic-skill-v001-gpt-5.5-low-"
POLICY_MIX_RUN_PREFIX = "synthetic-policy-mix-v001-gpt-5.5-low-"


def _relabel(source: Path, target: Path, scenario: str) -> None:
    text = (source / EXPERIMENT_PROJECTION_FILENAME).read_text(encoding="utf-8")
    text = text.replace(_RUN_PREFIX, POLICY_MIX_RUN_PREFIX).replace(LEGACY_SCENARIO, scenario)
    projection = ExperimentReviewProjection.model_validate_json(text)
    target.mkdir(parents=True, exist_ok=True)
    (target / EXPERIMENT_PROJECTION_FILENAME).write_text(
        projection.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )


def write_scenario_epoch(dest: Path, *, policy_mix_scenario: str) -> list[Path]:
    """Write the scenario-epoch experiments under ``dest``; return their directories."""
    written: list[Path] = []
    with tempfile.TemporaryDirectory() as scratch:
        for experiment_dir in generate_synthetic_benchmark(Path(scratch)):
            if f"__{LEGACY_SCENARIO}__" not in experiment_dir.name:
                continue
            target = dest / experiment_dir.name
            shutil.copytree(experiment_dir, target)
            written.append(target)
            if experiment_dir.name.endswith(_RELABELLED):
                relabelled = dest / experiment_dir.name.replace(LEGACY_SCENARIO, "policy-mix")
                _relabel(experiment_dir, relabelled, policy_mix_scenario)
                written.append(relabelled)
    return written
