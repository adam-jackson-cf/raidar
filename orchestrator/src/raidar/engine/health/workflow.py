"""Audit the workflow's judge and primitive evidence as one health slice."""

from raidar.engine.health.base import Audit
from raidar.engine.health.evidence import evidence_family
from raidar.engine.health.judge import check_judge
from raidar.engine.health.model import HealthCheck


def check_workflow(audit: Audit) -> tuple[HealthCheck, ...]:
    return (
        *check_judge(audit),
        *evidence_family(
            audit.trial_dir, audit.record.agent_override, [s.step for s in audit.attempted]
        ),
    )
