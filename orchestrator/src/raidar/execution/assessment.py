"""The experiment's assessment settings as a rescore config (spec -> scorers glue).

``experiment.yaml``'s ``assessment.inventory_policy`` and
``assessment.scorer_config`` are written next to every run as
``assessment-config.yaml``, the file ``raidar assess --rescore --config``
reads, so scoring a run uses exactly the locked assessor settings.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from raidar.scorers.inventory import InventoryPolicy
from raidar.scorers.rescore_config import RescoreConfig
from raidar.spec.schema import AssessmentSpec

ASSESSMENT_CONFIG_FILE = "assessment-config.yaml"


def rescore_config(assessment: AssessmentSpec) -> RescoreConfig:
    policy = assessment.inventory_policy
    return RescoreConfig(
        inventory_policy=InventoryPolicy(
            generated_globs=policy.generated_globs, test_globs=policy.test_globs
        ),
        scorers={ref: dict(config) for ref, config in assessment.scorer_config.items()},
    )


def write_assessment_config(assessment: AssessmentSpec, run_dir: Path) -> Path:
    path = run_dir / ASSESSMENT_CONFIG_FILE
    document = rescore_config(assessment).model_dump(mode="json")
    path.write_text(yaml.safe_dump(document, sort_keys=True))
    return path
