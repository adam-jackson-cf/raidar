"""The per-trial run-health audit record (``health.json``; mechanics-v1 design r2 §3.2).

Each check is ``{id, family, status, detail, evidence}``. ``status`` is ``pass``,
``violation`` or ``unmeasured``: a check that lacks evidence is ``unmeasured`` with its
reason, never ``pass``. The audit itself lives in ``raidar.engine.health.audit``;
``raidar.engine.trial.collect`` maps violations onto the trial's validity checks, so this
module holds only the record and its file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from raidar.contract.trial import ExecutionModel

HEALTH_FILE = "health.json"
HealthStatus = Literal["pass", "violation", "unmeasured"]
HealthFamily = Literal["V", "H", "L", "P", "T"]

PASS: HealthStatus = "pass"
VIOLATION: HealthStatus = "violation"
UNMEASURED: HealthStatus = "unmeasured"


class HealthCheck(ExecutionModel):
    id: str
    family: HealthFamily
    status: HealthStatus
    detail: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class HealthReport(ExecutionModel):
    kind: Literal["raidar-health"] = "raidar-health"
    schema_version: Literal[1] = 1
    trial_id: str
    checks: tuple[HealthCheck, ...]

    @property
    def violations(self) -> tuple[HealthCheck, ...]:
        return tuple(check for check in self.checks if check.status == VIOLATION)

    @property
    def unmeasured(self) -> tuple[HealthCheck, ...]:
        return tuple(check for check in self.checks if check.status == UNMEASURED)


def write_health(trial_dir: Path, report: HealthReport) -> Path:
    path = trial_dir / HEALTH_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
    return path


def load_health(trial_dir: Path) -> HealthReport | None:
    path = trial_dir / HEALTH_FILE
    if not path.is_file():
        return None
    return HealthReport.model_validate_json(path.read_text())
