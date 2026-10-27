"""Audit the workflow's gate, definition, judge and lab-evidence checks as one health slice."""

from raidar.engine.health.base import Audit
from raidar.engine.health.definition import check_definition
from raidar.engine.health.evidence import evidence_family
from raidar.engine.health.gate import check_gate
from raidar.engine.health.judge import check_judge
from raidar.engine.health.model import HealthCheck


def check_workflow(audit: Audit) -> tuple[HealthCheck, ...]:
    return (
        *check_gate(audit),
        *check_definition(audit),
        *check_judge(audit),
        *evidence_family(audit),
    )
