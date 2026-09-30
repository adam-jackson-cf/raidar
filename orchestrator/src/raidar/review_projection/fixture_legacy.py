"""Scenario-epoch experiments for the synthetic review fixtures.

The scenario-epoch producer (scenario runner, scorecards, weighted scorers) was
removed at the WS-L cutover. What remains is the read path: a scenario-epoch
experiment renders from its persisted ``review-projection.json``. The labelled
synthetic scenario-epoch projections that producer last wrote are frozen in
``scenario_epoch_fixture.json.gz`` (experiment directory name -> projection
text, byte for byte), so the old epoch keeps an observable regression net
without the producer:

- ``write_scenario_epoch`` writes the ``skill-benchmark-coding-test``
  experiments (three revisions, two agent specs) under the experiment fixture's
  ``<dest>/experiments``. Their run ids do not collide with the experiment
  fixture's. One of them is relabelled onto the experiment fixture's one-shot
  problem, so a compared family mixes the scenario-epoch composite with
  ``scb-canonical-oneshot@1`` and the headline-policy mismatch warning renders.
  Relabelling rewrites the scenario name and run ids in the projection; it is a
  synthetic comparison, labelled as such like every fixture payload.
- ``write_benchmark_fixture`` (``make benchmark-fixture-synthetic``) writes all
  nine frozen experiments, the scenario-epoch-only review-surface fixture.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

from raidar.review_projection.legacy import EXPERIMENT_PROJECTION_FILENAME
from raidar.schemas.review import ExperimentReviewProjection

FROZEN_FIXTURE = Path(__file__).with_name("scenario_epoch_fixture.json.gz")
LEGACY_SCENARIO = "skill-benchmark-coding-test"
_RELABELLED = f"__{LEGACY_SCENARIO}__v001__codex-cli__gpt-5.5-low"
_RUN_PREFIX = "synthetic-skill-v001-gpt-5.5-low-"
POLICY_MIX_RUN_PREFIX = "synthetic-policy-mix-v001-gpt-5.5-low-"


def frozen_projections() -> dict[str, str]:
    """Experiment directory name -> persisted projection text, schema-checked."""
    with gzip.open(FROZEN_FIXTURE, "rt", encoding="utf-8") as handle:
        bundle = json.load(handle)
    for name, text in bundle.items():
        try:
            ExperimentReviewProjection.model_validate_json(text)
        except ValueError as exc:
            raise ValueError(f"frozen scenario-epoch projection {name} fails the schema") from exc
    return dict(bundle)


def _write(target: Path, text: str) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    (target / EXPERIMENT_PROJECTION_FILENAME).write_text(text, encoding="utf-8")
    return target


def _relabelled(text: str, scenario: str) -> str:
    text = text.replace(_RUN_PREFIX, POLICY_MIX_RUN_PREFIX).replace(LEGACY_SCENARIO, scenario)
    projection = ExperimentReviewProjection.model_validate_json(text)
    return projection.model_dump_json(indent=2) + "\n"


def write_scenario_epoch(dest: Path, *, policy_mix_scenario: str) -> list[Path]:
    """Write the scenario-epoch experiments under ``dest``; return their directories."""
    written: list[Path] = []
    for name, text in sorted(frozen_projections().items()):
        if f"__{LEGACY_SCENARIO}__" not in name:
            continue
        written.append(_write(dest / name, text))
        if name.endswith(_RELABELLED):
            relabelled = dest / name.replace(LEGACY_SCENARIO, "policy-mix")
            written.append(_write(relabelled, _relabelled(text, policy_mix_scenario)))
    return written


def write_benchmark_fixture(dest: Path) -> list[Path]:
    """Write every frozen scenario-epoch experiment under ``dest``."""
    return [_write(dest / name, text) for name, text in sorted(frozen_projections().items())]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(
            "usage: python -m raidar.review_projection.fixture_legacy <experiments-root>",
            file=sys.stderr,
        )
        return 2
    for path in write_benchmark_fixture(Path(args[0])):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
