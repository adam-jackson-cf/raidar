"""The verifier's per-test coverage pass, run offline from the stored step snapshots.

In the trial the verifier grades every step twice: the grading run (the
verdict) and, on a fresh copy of the same snapshot, the coverage run whose
per-test contexts feed code-quality's G (``raidar.verifier.grader``). The
coverage run never changes the verdict, yet it sat on the trial's critical
path: in ``smoke-04`` of the guardrails experiment it took about 1077 s of
each trial's 8 steps, more than the baseline arm's whole agent time. A trial
whose lock sets ``protocol.verifier_coverage: offline`` grades without it
(``record.json`` ``coverage.enabled`` false), and this module runs it later:

- ``produce_coverage_path`` (run by ``raidar assess --rescore`` when a locked
  scorer reads it, ``code-quality@2``): for every graded step of the target
  whose record says the coverage pass was not run, the stored snapshot is
  checked against its recorded digest (and the bundle against its digest) and
  the coverage run is made with the step's own bundle, sandbox and grader
  timeout, exactly as the in-trial pass makes it (``grader.run_oracle`` with
  coverage). Its parity is checked against the trial's own grading run, read
  back from the step's ``evidence.json``; a step whose grading run failed is
  ``ineligible`` without a coverage run, as in the trial. The parity verdict,
  its differences and the fork-shaped G input (``records._fork_payload``, the
  same payload the trial writes as ``g-kernel-input.json``) go to the
  Assessment store (``raidar.verifier.coverage_store``), never the job. A run
  already stored is not made again;
- ``check_coverage_path`` (``raidar assess --regrade --coverage-check``): makes
  the coverage run offline for every graded step and compares it, without
  writing anything, with the trial's own coverage pass (a step that had one:
  parity verdict, differences and G input) or with the stored offline run.
  It is the proof that the offline pass reproduces the in-trial one.

Nothing in the job directory is written.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from raidar.verifier.coverage_store import (
    COVERAGE_KIND,
    SCHEMA_VERSION,
    coverage_path,
    read_offline_coverage,
)
from raidar.verifier.grader import _parity, run_oracle
from raidar.verifier.models import (
    CaseResult,
    InfraFailure,
    ParityVerdict,
    RunEvidence,
)
from raidar.verifier.outcomes import RunOutcome, StepGrade
from raidar.verifier.parity import ineligible
from raidar.verifier.per_trial import per_trial
from raidar.verifier.records import (
    EVIDENCE_FILE,
    FORK_EVIDENCE_FILE,
    RECORD_FILE,
    VERIFIER_RECORD_DIR,
    StepRecord,
    _fork_payload,
    load_step_record,
)
from raidar.verifier.regrade import (
    RegradeError,
    RegradeOptions,
    _bundle,
    _snapshot,
    docker_sandbox_factory,
    trial_dirs,
)

PRODUCTION_KIND = "raidar-offline-coverage-production"
CHECK_KIND = "raidar-offline-coverage-check"


@dataclass(frozen=True)
class CoverageTarget:
    """One graded step of a trial whose coverage run is made (or checked) offline."""

    trial_dir: Path
    record_dir: Path
    record: StepRecord

    @property
    def deferred(self) -> bool:
        """The trial graded this step without its coverage pass."""
        return not self.record.coverage.enabled


@dataclass(frozen=True)
class CoverageRun:
    """What an offline coverage run decided, as the in-trial pass would have recorded it."""

    parity: ParityVerdict
    differences: tuple[str, ...]
    fork: dict[str, Any] | None
    environment_id: str
    seconds: float
    infra_failure: InfraFailure | None


def coverage_targets(trial_dir: Path) -> list[CoverageTarget]:
    """Every graded step of the trial, in step order."""
    steps_root = trial_dir / VERIFIER_RECORD_DIR / "steps"
    if not steps_root.is_dir():
        return []
    return [
        CoverageTarget(trial_dir, record_dir, load_step_record(record_dir))
        for record_dir in sorted(p for p in steps_root.iterdir() if (p / RECORD_FILE).is_file())
    ]


def _grading_run(target: CoverageTarget) -> RunOutcome:
    """The trial's own grading run, read back from the step's ``evidence.json``."""
    path = target.record_dir / EVIDENCE_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))["grading"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RegradeError(f"the step's grading evidence is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RegradeError(f"the step's grading evidence is missing: {path}")
    infra = payload.get("infra_failure")
    evidence = payload.get("evidence")
    return RunOutcome(
        role="grading",
        cases=tuple(CaseResult.model_validate(case) for case in payload.get("cases") or ()),
        infra_failure=InfraFailure.model_validate(infra) if infra else None,
        evidence=RunEvidence.model_validate(evidence) if evidence else None,
        timing_sensitive=target.record.timing_sensitive_cases,
        seconds=0.0,
    )


async def run_offline_coverage(target: CoverageTarget, options: RegradeOptions) -> CoverageRun:
    """The step's coverage run from its stored snapshot, parity-checked against the trial."""
    record = target.record
    snapshot = _snapshot(target.trial_dir, record)
    grading = _grading_run(target)
    bundle = _bundle(record, options)
    factory = options.sandbox_factory or docker_sandbox_factory(options.docker_context)
    sandbox = await factory(bundle)
    environment = await sandbox.environment_id()
    if grading.infra_failure is not None:
        # As in the trial: a failed grading run gets no coverage run.
        verdict = ineligible(f"grading_infra_failure:{grading.infra_failure.code}")
        return CoverageRun(verdict, (), None, environment, 0.0, None)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="raidar-coverage-", dir=options.work_root) as work:
        measured = await run_oracle(
            bundle,
            bundle.step(record.step),
            snapshot,
            sandbox,
            Path(work) / "coverage",
            coverage=True,
            timeout_s=record.grader.timeout_s,
        )
    verdict, differences = _parity(grading, measured)
    fork = _fork_payload(StepGrade(record.grade, grading, measured, verdict, differences))
    # As the trial writes it (json.dumps, sort_keys) and reads it back: plain JSON values.
    fork = json.loads(json.dumps(fork, sort_keys=True)) if fork is not None else None
    return CoverageRun(
        verdict,
        differences,
        fork,
        environment,
        time.perf_counter() - started,
        measured.infra_failure,
    )


def _entry(target: CoverageTarget, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "trial_dir": str(target.trial_dir),
        "step": target.record.step,
        "snapshot_digest": target.record.snapshot.digest,
        "status": status,
        **extra,
    }


def _payload(target: CoverageTarget, run: CoverageRun) -> dict[str, Any]:
    record = target.record
    return {
        "kind": COVERAGE_KIND,
        "schema_version": SCHEMA_VERSION,
        "step": record.step,
        "checkpoint": record.checkpoint,
        "snapshot_digest": record.snapshot.digest,
        "bundle_digest": record.bundle.digest,
        "grader_environment_id": run.environment_id,
        "trial_grader_environment_id": record.grader.environment_id,
        "timeout_s": record.grader.timeout_s,
        "produced_at": datetime.now(UTC).isoformat(),
        "coverage_run_s": run.seconds,
        "coverage_infra_failure": (
            run.infra_failure.model_dump(mode="json") if run.infra_failure else None
        ),
        "parity": run.parity.model_dump(mode="json"),
        "differences": list(run.differences),
        "fork": run.fork,
    }


async def produce_offline_coverage(
    target: CoverageTarget, store_root: Path, options: RegradeOptions
) -> dict[str, Any]:
    """Make one deferred step's coverage run into the store (``stored`` when it already is)."""
    record = target.record
    path = coverage_path(store_root, record.bundle.digest, record.step, record.snapshot.digest)
    if path.is_file():
        return _entry(target, "stored", path=str(path))
    try:
        run = await run_offline_coverage(target, options)
    except RegradeError as exc:
        return _entry(target, "error", error=str(exc))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload(target, run)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return _entry(
        target,
        "produced",
        path=str(path),
        eligible=run.parity.eligible,
        coverage_run_s=round(run.seconds, 3),
    )


def _trials(path: Path) -> list[Path]:
    try:
        return trial_dirs(path)
    except RegradeError:
        return []


async def produce_coverage_path(
    path: Path, store_root: Path, options: RegradeOptions, concurrency: int = 1
) -> dict[str, Any]:
    """Make every deferred coverage run under a trial or job dir into the store.

    Up to ``concurrency`` trials are worked on at once; each trial's steps run in order.
    """

    async def trial_runs(trial: Path) -> list[dict[str, Any]]:
        return [
            await produce_offline_coverage(target, store_root, options)
            for target in coverage_targets(trial)
            if target.deferred
        ]

    entries = await per_trial(_trials(path), trial_runs, concurrency)
    return {
        "kind": PRODUCTION_KIND,
        "steps": entries,
        "errors": sum(1 for entry in entries if entry["status"] == "error"),
    }


# ---- the check: the offline run against what the trial (or the store) recorded ------------


Recorded = tuple[ParityVerdict | None, tuple[str, ...], Any]
"""A recorded coverage pass: parity verdict, its differences and the G input (None: absent)."""


def _in_trial(target: CoverageTarget) -> Recorded:
    """The trial's own coverage pass: parity, differences and G input (None when absent)."""
    fork_path = target.record_dir / FORK_EVIDENCE_FILE
    fork = json.loads(fork_path.read_text(encoding="utf-8")) if fork_path.is_file() else None
    coverage = target.record.coverage
    return coverage.parity, tuple(coverage.differences), fork


def _differences(expected: Recorded, run: CoverageRun) -> list[str]:
    parity, differences, fork = expected
    found: list[str] = []
    if parity != run.parity:
        found.append(f"parity: {parity} -> {run.parity}")
    if differences != run.differences:
        found.append(f"parity differences: {list(differences)} -> {list(run.differences)}")
    if fork != run.fork:
        found.append(f"{FORK_EVIDENCE_FILE}: {_fork_change(fork, run.fork)}")
    return found


def _fork_change(expected: Any, produced: Any) -> str:
    if expected is None or produced is None:
        return f"{'absent' if expected is None else 'present'} -> " + (
            "absent" if produced is None else "present"
        )
    changed = sorted(
        key for key in set(expected) | set(produced) if expected.get(key) != produced.get(key)
    )
    return f"differs in {', '.join(changed)}"


async def check_offline_coverage(
    target: CoverageTarget, store_root: Path | None, options: RegradeOptions
) -> dict[str, Any]:
    """Make the step's coverage run offline and compare it with the recorded one."""
    record = target.record
    if target.deferred:
        stored = (
            read_offline_coverage(
                store_root, record.bundle.digest, record.step, record.snapshot.digest
            )
            if store_root is not None
            else None
        )
        if stored is None:
            return _entry(target, "not_produced", against="store")
        expected: Recorded = (stored.parity, stored.differences, stored.fork)
        against = "store"
    else:
        expected = _in_trial(target)
        against = "trial"
    try:
        run = await run_offline_coverage(target, options)
    except RegradeError as exc:
        return _entry(target, "error", against=against, error=str(exc))
    differences = _differences(expected, run)
    return _entry(
        target,
        "mismatch" if differences else "match",
        against=against,
        differences=differences,
        eligible=run.parity.eligible,
        coverage_run_s=round(run.seconds, 3),
    )


async def check_coverage_path(
    path: Path, store_root: Path | None, options: RegradeOptions, concurrency: int = 1
) -> dict[str, Any]:
    """Check every graded step's offline coverage run under a trial or job dir.

    Up to ``concurrency`` trials are checked at once; each trial's steps run in order.
    """

    async def trial_checks(trial: Path) -> list[dict[str, Any]]:
        return [
            await check_offline_coverage(target, store_root, options)
            for target in coverage_targets(trial)
        ]

    entries = await per_trial(trial_dirs(path), trial_checks, concurrency)
    return {
        "kind": CHECK_KIND,
        "match": bool(entries) and all(entry["status"] == "match" for entry in entries),
        "steps": entries,
    }


__all__ = [
    "CoverageRun",
    "CoverageTarget",
    "check_coverage_path",
    "check_offline_coverage",
    "coverage_targets",
    "produce_coverage_path",
    "produce_offline_coverage",
    "run_offline_coverage",
]
