"""The experiment's assessment settings as a rescore config (spec -> scorers glue).

``experiment.yaml``'s ``assessment.inventory_policy`` and
``assessment.scorer_config`` are written next to every job as
``assessment-config.yaml``, the file ``raidar assess --rescore --config``
reads, so scoring a run uses exactly the locked assessor settings.

The protocol the trials ran under reaches the scorers that read it: every
``protocol_config`` key a scorer's registration declares (``correctness@1``:
``progression``) is set from ``protocol``. The spec already rejects an
explicit scorer config that contradicts the protocol. Problem facts
(``entry_file``) are not written here: they travel with each trial's oracle
bundle, so a run over several problems still scores each with its own.

Problem errata the lock pins (``execution.references.problem.<ref>.metadata.errata``)
reach every scorer whose registration takes them (``correctness@1``) as config
key ``errata``, ``{problem ref: ErrataRef}``. A lock that pins no errata adds no
key, so its config, and the Assessment keys derived from it, are unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from raidar.design.experiment.schema import AssessmentSpec, ProtocolSpec
from raidar.design.problem.errata import errata_ref_from_metadata
from raidar.grading.errata import ErrataRef
from raidar.scoring.framework.catalog import (
    ERRATA_CONFIG_KEY,
    ScorerRegistry,
    default_scorer_registry,
)
from raidar.scoring.framework.inventory import InventoryPolicy
from raidar.scoring.framework.rescore_config import RescoreConfig

ASSESSMENT_CONFIG_FILE = "assessment-config.yaml"


ProblemErrata = Mapping[str, ErrataRef]
"""``{problem ref: ErrataRef}``: the errata a lock pins, per problem."""


def lock_errata(lock: Mapping[str, Any]) -> dict[str, ErrataRef]:
    """The errata a lock pins for each of its problems (problems without one are absent)."""
    execution = lock.get("execution")
    references = execution.get("references") if isinstance(execution, Mapping) else None
    problems = references.get("problem") if isinstance(references, Mapping) else None
    pinned: dict[str, ErrataRef] = {}
    for ref, record in sorted((problems or {}).items()):
        errata = errata_ref_from_metadata(record.get("metadata"))
        if errata is not None:
            pinned[ref] = errata
    return pinned


def _scorer_configs(
    assessment: AssessmentSpec,
    protocol: ProtocolSpec,
    registry: ScorerRegistry,
    errata: ProblemErrata,
) -> dict[str, dict[str, object]]:
    settings = protocol.model_dump(mode="json")
    configs: dict[str, dict[str, object]] = {}
    for ref in assessment.scorers:
        registration = registry.get(ref)
        from_protocol = {name: settings[name] for name in registration.protocol_config}
        pinned = {key: value.model_dump(mode="json") for key, value in errata.items()}
        from_lock: dict[str, object] = (
            {ERRATA_CONFIG_KEY: pinned} if registration.errata_config and pinned else {}
        )
        explicit = dict(assessment.scorer_config.get(ref, {}))
        if from_protocol or from_lock or explicit:
            configs[ref] = {**from_protocol, **explicit, **from_lock}
    return configs


def rescore_config(
    assessment: AssessmentSpec,
    protocol: ProtocolSpec,
    registry: ScorerRegistry | None = None,
    errata: ProblemErrata | None = None,
) -> RescoreConfig:
    policy = assessment.inventory_policy
    return RescoreConfig(
        inventory_policy=InventoryPolicy(
            generated_globs=policy.generated_globs, test_globs=policy.test_globs
        ),
        scorers=_scorer_configs(
            assessment, protocol, registry or default_scorer_registry(), errata or {}
        ),
    )


def lock_rescore_config(
    lock: Mapping[str, Any], registry: ScorerRegistry | None = None
) -> RescoreConfig:
    """The assessor settings a lock declares (its effective spec's assessment section).

    Analysis and rescoring derive their config from the lock, not from the
    run's ``assessment-config.yaml``: relocking with new inventory or scorer
    settings keeps ``lock_sha256``, and the job's file only records what the
    settings were when the job executed.
    """
    spec = lock.get("spec")
    if not isinstance(spec, Mapping):
        raise ValueError("the lock has no spec")
    return rescore_config(
        AssessmentSpec.model_validate(spec.get("assessment") or {}),
        ProtocolSpec.model_validate(spec.get("protocol") or {}),
        registry,
        lock_errata(lock),
    )


def write_assessment_config(
    assessment: AssessmentSpec,
    protocol: ProtocolSpec,
    job_dir: Path,
    errata: ProblemErrata | None = None,
) -> Path:
    path = job_dir / ASSESSMENT_CONFIG_FILE
    document = rescore_config(assessment, protocol, errata=errata).model_dump(mode="json")
    path.write_text(yaml.safe_dump(document, sort_keys=True))
    return path
