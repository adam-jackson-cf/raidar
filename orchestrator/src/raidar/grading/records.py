"""Host-only verifier records for a trial.

Records are written under ``<trial_dir>/raidar-oracle/steps/<step>/`` on the
host, which is never copied into the agent environment. Harbor downloads the
container's ``/logs/verifier`` into ``<trial_dir>/verifier``, so nothing about
verifier outcomes is written there: the agent (or a process it left running)
must never see verifier results (F4). Harbor receives only the reward.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from raidar.grading.errata import ErrataOutcome, ErrataRef, errata_outcome
from raidar.grading.leak import LeakReport
from raidar.grading.models import CaseResult, ParityVerdict, VerifierGrade, VerifierModel
from raidar.grading.outcomes import RunOutcome, StepGrade
from raidar.grading.parity import fork_regression_run_evidence
from raidar.grading.submission_deps import SubmissionDependencies

VERIFIER_RECORD_DIR = "raidar-oracle"
RECORD_FILE = "record.json"
EVIDENCE_FILE = "evidence.json"
FORK_EVIDENCE_FILE = "g-kernel-input.json"
PRE_AGENT_LEAK_FILE = "pre-agent-leak.json"


def step_record_dir(trial_dir: Path, step: str) -> Path:
    return trial_dir / VERIFIER_RECORD_DIR / "steps" / step


def stored_snapshot_path(step: str | None) -> str:
    """Where Harbor archives the step's ``/app`` artifact, relative to the trial dir."""
    return f"steps/{step}/artifacts/app" if step else "artifacts/app"


class BundleRef(VerifierModel):
    root: str
    digest: str
    problem: str


class SnapshotRef(VerifierModel):
    requested: Literal["artifact", "download"]
    used: Literal["artifact", "download"]
    digest: str
    stored_path: str


class GraderRef(VerifierModel):
    image: str
    environment_id: str
    image_built: bool
    timeout_s: float = Field(gt=0)


class CoverageRef(VerifierModel):
    enabled: bool
    parity: ParityVerdict | None = None
    differences: tuple[str, ...] = ()


class StepRecord(VerifierModel):
    kind: Literal["raidar-oracle-step"] = "raidar-oracle-step"
    schema_version: Literal[1] = 1
    step: str
    checkpoint: str
    bundle: BundleRef
    snapshot: SnapshotRef
    grader: GraderRef
    grade: VerifierGrade
    summary: dict[str, object]
    timing_sensitive_cases: tuple[str, ...] = ()
    coverage: CoverageRef
    leak_check: LeakReport | None = None
    pre_agent_leak_check: LeakReport | None = None
    rewards: dict[str, float]
    timings: dict[str, float] = Field(default_factory=dict)
    errata: ErrataOutcome | None = None
    """The errata-applied verdict when the trial ran under a pinned errata; ``grade``
    and ``rewards`` stay the raw SCB verdict either way."""
    submission_dependencies: SubmissionDependencies | None = None


def _run_payload(run: RunOutcome | None) -> dict[str, object] | None:
    if run is None:
        return None
    return {
        "role": run.role,
        "infra_failure": run.infra_failure.model_dump() if run.infra_failure else None,
        "cases": [case.model_dump() for case in run.cases],
        "evidence": run.evidence.model_dump(mode="json") if run.evidence else None,
    }


def fork_payload(result: StepGrade) -> dict[str, object] | None:
    grading, coverage = result.grading, result.coverage
    if grading.evidence is None or coverage is None or coverage.evidence is None:
        return None
    cases: tuple[CaseResult, ...] = grading.cases
    return {
        "transition_step": result.grade.step,
        "baseline": fork_regression_run_evidence(grading.evidence, cases),
        "produced": fork_regression_run_evidence(coverage.evidence, coverage.cases),
    }


def parse_errata(errata: Mapping[str, Any] | None) -> ErrataRef | None:
    """The verifier's ``errata`` argument (the lock's pinned errata), validated."""
    return ErrataRef.model_validate(dict(errata)) if errata is not None else None


def errata_for(grade: VerifierGrade, errata: ErrataRef | None) -> ErrataOutcome | None:
    """The record's errata section: the errata-applied verdict, when an errata is pinned."""
    return errata_outcome(grade, errata) if errata is not None else None


def build_step_record(
    result: StepGrade,
    *,
    bundle: BundleRef,
    snapshot: SnapshotRef,
    grader: GraderRef,
    coverage_enabled: bool,
    leaks: tuple[LeakReport, LeakReport | None],
    rewards: dict[str, float],
    timings: dict[str, float],
    errata: ErrataRef | None,
) -> StepRecord:
    """The host-only record of one graded step (``leaks``: final, then pre-agent scan)."""
    return StepRecord(
        step=result.grade.step,
        checkpoint=result.grade.checkpoint,
        bundle=bundle,
        snapshot=snapshot,
        grader=grader,
        grade=result.grade,
        summary=result.grade.summary(),
        timing_sensitive_cases=result.grading.timing_sensitive,
        coverage=CoverageRef(
            enabled=coverage_enabled, parity=result.parity, differences=result.parity_differences
        ),
        leak_check=leaks[0],
        pre_agent_leak_check=leaks[1],
        rewards=rewards,
        timings=dict(timings),
        errata=errata_for(result.grade, errata),
        submission_dependencies=result.grading.dependencies,
    )


def write_step_evidence(record_dir: Path, result: StepGrade) -> None:
    record_dir.mkdir(parents=True, exist_ok=True)
    evidence = {"grading": _run_payload(result.grading), "coverage": _run_payload(result.coverage)}
    (record_dir / EVIDENCE_FILE).write_text(json.dumps(evidence, sort_keys=True) + "\n")
    fork = fork_payload(result)
    if fork is not None:
        (record_dir / FORK_EVIDENCE_FILE).write_text(json.dumps(fork, sort_keys=True) + "\n")


def write_step_record(record_dir: Path, record: StepRecord) -> None:
    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / RECORD_FILE).write_text(record.model_dump_json(indent=2) + "\n")


def write_pre_agent_leak(record_dir: Path, report: LeakReport) -> None:
    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / PRE_AGENT_LEAK_FILE).write_text(report.model_dump_json(indent=2) + "\n")


def load_pre_agent_leak(record_dir: Path) -> LeakReport | None:
    path = record_dir / PRE_AGENT_LEAK_FILE
    return LeakReport.model_validate_json(path.read_text()) if path.is_file() else None


def load_step_record(record_dir: Path) -> StepRecord:
    return StepRecord.model_validate_json((record_dir / RECORD_FILE).read_text())
