"""The experiment's assessment settings as a rescore config (spec -> scorers glue).

``experiment.yaml``'s ``assessment.inventory_policy`` and
``assessment.scorer_config`` are written next to every run as
``assessment-config.yaml``, the file ``raidar assess --rescore --config``
reads, so scoring a run uses exactly the locked assessor settings.

The protocol the trials ran under reaches the scorers that read it: every
``protocol_config`` key a scorer's registration declares (``correctness@1``:
``progression``) is set from ``protocol``. The spec already rejects an
explicit scorer config that contradicts the protocol. Problem facts
(``entry_file``) are not written here: they travel with each trial's oracle
bundle, so a run over several problems still scores each with its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from raidar.scorers.catalog import ScorerRegistry, default_scorer_registry
from raidar.scorers.inventory import InventoryPolicy
from raidar.scorers.rescore_config import RescoreConfig
from raidar.spec.schema import AssessmentSpec, ProtocolSpec

ASSESSMENT_CONFIG_FILE = "assessment-config.yaml"


def _scorer_configs(
    assessment: AssessmentSpec, protocol: ProtocolSpec, registry: ScorerRegistry
) -> dict[str, dict[str, object]]:
    settings = protocol.model_dump(mode="json")
    configs: dict[str, dict[str, object]] = {}
    for ref in assessment.scorers:
        from_protocol = {name: settings[name] for name in registry.get(ref).protocol_config}
        explicit = dict(assessment.scorer_config.get(ref, {}))
        if from_protocol or explicit:
            configs[ref] = {**from_protocol, **explicit}
    return configs


def rescore_config(
    assessment: AssessmentSpec,
    protocol: ProtocolSpec,
    registry: ScorerRegistry | None = None,
) -> RescoreConfig:
    policy = assessment.inventory_policy
    return RescoreConfig(
        inventory_policy=InventoryPolicy(
            generated_globs=policy.generated_globs, test_globs=policy.test_globs
        ),
        scorers=_scorer_configs(assessment, protocol, registry or default_scorer_registry()),
    )


def lock_rescore_config(
    lock: Mapping[str, Any], registry: ScorerRegistry | None = None
) -> RescoreConfig:
    """The assessor settings a lock declares (its effective spec's assessment block).

    Analysis and rescoring derive their config from the lock, not from the
    run's ``assessment-config.yaml``: relocking with new inventory or scorer
    settings keeps ``lock_sha256``, and the run's file only records what the
    settings were when the run executed.
    """
    spec = lock.get("spec")
    if not isinstance(spec, Mapping):
        raise ValueError("the lock has no spec")
    return rescore_config(
        AssessmentSpec.model_validate(spec.get("assessment") or {}),
        ProtocolSpec.model_validate(spec.get("protocol") or {}),
        registry,
    )


def write_assessment_config(
    assessment: AssessmentSpec, protocol: ProtocolSpec, run_dir: Path
) -> Path:
    path = run_dir / ASSESSMENT_CONFIG_FILE
    document = rescore_config(assessment, protocol).model_dump(mode="json")
    path.write_text(yaml.safe_dump(document, sort_keys=True))
    return path
