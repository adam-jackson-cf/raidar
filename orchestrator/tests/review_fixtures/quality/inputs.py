"""Materialize labelled Quality fixtures' stored snapshots and verifier input records."""

from __future__ import annotations

import json
from pathlib import Path

from raidar.contract.snapshot import tree_digest
from raidar.contract.trial import SnapshotInfo, TrialRecord
from raidar.grading.bundle import load_bundle
from raidar.grading.models import CaseGroup, CaseResult, CaseStatus, VerifierGrade
from raidar.grading.records import (
    BundleRef,
    CoverageRef,
    GraderRef,
    SnapshotRef,
    StepRecord,
    write_step_record,
)


def write_inputs(job: Path, record: TrialRecord, bundle_root: Path) -> TrialRecord:
    """Write the snapshots/verifier inputs a real Assessment lookup reads."""
    trial = job / record.trial_dir
    bundle = load_bundle(bundle_root)
    results = []
    for step in record.step_results:
        relative = f"steps/{step.step}/artifacts/app"
        snapshot = trial / relative
        snapshot.mkdir(parents=True, exist_ok=True)
        for name in ("core", "report", "cli"):
            content = f"# synthetic {record.trial.trial_id} {step.step}\n" + "value = 1\n" * 600
            path = snapshot / "ledger" / f"{name}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        digest = tree_digest(snapshot)
        grade = VerifierGrade(
            step=step.step,
            checkpoint=step.checkpoint,
            cases=(
                CaseResult(
                    id=f"test_{step.step}",
                    checkpoint_origin=step.checkpoint,
                    group=CaseGroup.CORE,
                    status=CaseStatus.PASSED if step.strict_pass_rate == 1 else CaseStatus.FAILED,
                ),
            ),
        )
        verifier = StepRecord(
            step=step.step,
            checkpoint=step.checkpoint,
            bundle=BundleRef(
                root=str(bundle_root), digest=bundle.digest(), problem=record.trial.problem
            ),
            snapshot=SnapshotRef(
                requested="artifact",
                used="artifact",
                digest=digest,
                stored_path=relative,
            ),
            grader=GraderRef(
                image="synthetic",
                environment_id="synthetic",
                image_built=False,
                timeout_s=900,
            ),
            grade=grade,
            summary=grade.summary(),
            coverage=CoverageRef(enabled=False),
            rewards=grade.rewards(),
        )
        write_step_record(trial / "raidar-oracle" / "steps" / step.step, verifier)
        results.append(
            step.model_copy(update={"snapshot": SnapshotInfo(path=relative, digest=digest)})
        )
    (trial / "result.json").write_text(
        json.dumps({"step_results": [{"step_name": step.step} for step in record.step_results]}),
        encoding="utf-8",
    )
    return record.model_copy(update={"step_results": tuple(results)})
