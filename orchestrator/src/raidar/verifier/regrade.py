"""Offline regrade: rerun the verifier over a finished trial's stored snapshots (E3).

For each step with a host-side verifier record, the stored ``/app`` snapshot is
verified against the recorded digest, the bundle against its recorded digest,
and the step is graded again (with the coverage pass when the original had it).
The regrade runs with the recorded grader timeout and matches when the grader
environment (image ID), case results, infrastructure verdict and parity
verdict are identical, so a result from a rebuilt or different grader image is
reported as a mismatch with its cause, never as a silent match (N3). The trial
directory is never modified.

Why not ``harbor trials regrade`` / ``harbor jobs regrade``: Harbor 0.23's
regrade only accepts tasks whose verifiers resolve to ``separate`` mode (it
starts a verifier environment over the seeded artifacts), while this verifier
runs host-side in shared mode (overhead-baseline decision) and requires a
live-environment leak check. It also writes a new trial and reports only a
reward delta, whereas N3 needs a per-case comparison against the stored record.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from raidar.core.snapshot import tree_digest
from raidar.verifier.bundle import VerifierBundle, load_bundle
from raidar.verifier.grader import grade_step
from raidar.verifier.grader_image import ensure_grader_image
from raidar.verifier.outcomes import StepGrade
from raidar.verifier.records import RECORD_FILE, VERIFIER_RECORD_DIR, StepRecord, load_step_record
from raidar.verifier.sandbox import DockerCopySandbox, GradingSandbox

SandboxFactory = Callable[[VerifierBundle], Awaitable[GradingSandbox]]


class RegradeError(RuntimeError):
    """The stored run cannot be regraded faithfully."""


@dataclass(frozen=True)
class StepRegrade:
    step: str
    match: bool
    differences: tuple[str, ...]
    original: dict[str, object]
    regraded: dict[str, object]


@dataclass(frozen=True)
class TrialRegrade:
    trial_dir: Path
    steps: tuple[StepRegrade, ...]

    @property
    def match(self) -> bool:
        return all(step.match for step in self.steps)


@dataclass
class RegradeOptions:
    docker_context: str
    bundle_override: Path | None = None
    work_root: Path | None = None
    sandbox_factory: SandboxFactory | None = None
    bundle_cache: dict[str, VerifierBundle] = field(default_factory=dict, repr=False)


def docker_sandbox_factory(docker_context: str) -> SandboxFactory:
    async def build(bundle: VerifierBundle) -> GradingSandbox:
        image = await ensure_grader_image(bundle.manifest.grader, docker_context=docker_context)
        return DockerCopySandbox(image=image.tag, docker_context=docker_context)

    return build


def write_store_json(path: Path, payload: dict[str, object]) -> None:
    """Write one Assessment-store file by atomic replace: an interrupted run never leaves a
    partial file that a later run would take for a stored grade."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def trial_dirs(path: Path) -> list[Path]:
    """A trial dir, or every trial with verifier records under a job dir."""
    if (path / VERIFIER_RECORD_DIR).is_dir():
        return [path]
    found = sorted(child for child in path.iterdir() if (child / VERIFIER_RECORD_DIR).is_dir())
    if not found:
        raise RegradeError(f"no {VERIFIER_RECORD_DIR}/ records under {path}")
    return found


def recorded_bundle(record: StepRecord, options: RegradeOptions) -> VerifierBundle:
    root = options.bundle_override or Path(record.bundle.root)
    key = str(root)
    if key not in options.bundle_cache:
        options.bundle_cache[key] = load_bundle(root)
    bundle = options.bundle_cache[key]
    if bundle.digest() != record.bundle.digest:
        raise RegradeError(
            f"bundle at {root} does not match recorded digest {record.bundle.digest}"
        )
    return bundle


def stored_snapshot(trial_dir: Path, record: StepRecord) -> Path:
    snapshot = trial_dir / record.snapshot.stored_path
    if not snapshot.is_dir():
        raise RegradeError(f"stored snapshot missing: {snapshot}")
    if tree_digest(snapshot) != record.snapshot.digest:
        raise RegradeError(f"stored snapshot changed since grading: {snapshot}")
    return snapshot


def compare(record: StepRecord, result: StepGrade, environment_id: str) -> tuple[str, ...]:
    differences: list[str] = []
    if record.grader.environment_id != environment_id:
        differences.append(
            f"grader_environment: {record.grader.environment_id} -> {environment_id}"
        )
    original = {case.id: case for case in record.grade.cases}
    regraded = {case.id: case for case in result.grade.cases}
    differences += [f"{k}: missing on regrade" for k in sorted(original.keys() - regraded.keys())]
    differences += [f"{k}: new on regrade" for k in sorted(regraded.keys() - original.keys())]
    differences += [
        f"{k}: {original[k].status.value} -> {regraded[k].status.value}"
        for k in sorted(original.keys() & regraded.keys())
        if original[k] != regraded[k]
    ]
    if record.grade.infra_failure != result.grade.infra_failure:
        differences.append(
            f"infra_failure: {record.grade.infra_failure} -> {result.grade.infra_failure}"
        )
    if record.coverage.parity != result.parity:
        differences.append(f"parity: {record.coverage.parity} -> {result.parity}")
    return tuple(differences)


async def regrade_step(trial_dir: Path, record_dir: Path, options: RegradeOptions) -> StepRegrade:
    record = load_step_record(record_dir)
    bundle = recorded_bundle(record, options)
    snapshot = stored_snapshot(trial_dir, record)
    factory = options.sandbox_factory or docker_sandbox_factory(options.docker_context)
    sandbox = await factory(bundle)
    with tempfile.TemporaryDirectory(prefix="raidar-regrade-", dir=options.work_root) as work:
        result = await grade_step(
            bundle,
            record.step,
            snapshot,
            sandbox,
            Path(work),
            coverage=record.coverage.enabled,
            timeout_s=record.grader.timeout_s,
        )
    differences = compare(record, result, await sandbox.environment_id())
    return StepRegrade(
        step=record.step,
        match=not differences,
        differences=differences,
        original=record.summary,
        regraded=result.grade.summary(),
    )


async def regrade_trial(trial_dir: Path, options: RegradeOptions) -> TrialRegrade:
    steps_root = trial_dir / VERIFIER_RECORD_DIR / "steps"
    record_dirs = sorted(p for p in steps_root.iterdir() if (p / RECORD_FILE).is_file())
    if not record_dirs:
        raise RegradeError(f"no verifier step records under {steps_root}")
    return TrialRegrade(
        trial_dir=trial_dir,
        steps=tuple([await regrade_step(trial_dir, d, options) for d in record_dirs]),
    )


def report_payload(results: list[TrialRegrade]) -> dict[str, object]:
    return {
        "kind": "raidar-oracle-regrade",
        "match": all(trial.match for trial in results),
        "trials": [
            {
                "trial_dir": str(trial.trial_dir),
                "match": trial.match,
                "steps": [
                    {
                        "step": step.step,
                        "match": step.match,
                        "differences": list(step.differences),
                        "original": step.original,
                        "regraded": step.regraded,
                    }
                    for step in trial.steps
                ],
            }
            for trial in results
        ],
    }


async def regrade_path(path: Path, options: RegradeOptions) -> dict[str, object]:
    return report_payload([await regrade_trial(trial, options) for trial in trial_dirs(path)])
