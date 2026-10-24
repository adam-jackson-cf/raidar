"""Raidar verifier: a host-side Harbor ``BaseVerifier`` (ADR-001 §2.3).

Loaded per trial through ``TrialConfig.verifier``::

    VerifierConfig(
        import_path="raidar.grading.verifier:RaidarVerifier",
        kwargs={"oracle_bundle": "<host bundle dir>", ...},
    )

Per step it (1) asserts no hidden test is present in the agent environment,
both now and (via the report ``raidar.grading.guard.install_leak_guard``
stored) before the agent started, (2) takes the step's ``/app`` snapshot
(from the one ``snapshot_source`` the protocol names: Harbor's host artifact, or an explicit
``download``; a missing or unusable artifact is an infrastructure failure, never a download;
digest always recorded), (3) grades a copy in a fresh
no-network grader container and, optionally, (4) runs the separate coverage
pass. It returns only the reward to Harbor and writes its records host-side.
Tests are never uploaded to the agent environment.

With ``errata`` (the lock's pinned ``ErrataRef``) the record also carries the
errata-applied verdict (``raidar.grading.errata``). The reward stays the raw
verdict, so progression gates and every raw outcome are unchanged.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

from harbor.models.verifier.result import VerifierResult
from harbor.verifier.base import BaseVerifier

from raidar.contract.snapshot import (
    APP_ARTIFACT_DESTINATION,
    APP_SOURCE,
    SNAPSHOT_EXCLUDES,
    tree_digest,
)
from raidar.grading.bundle import VerifierBundle, load_bundle
from raidar.grading.grader import DEFAULT_RUN_TIMEOUT_S, grade_step
from raidar.grading.grader_image import ensure_grader_image
from raidar.grading.leak import LeakReport, LeakScanError, scan_agent_environment
from raidar.grading.models import INFRA_REWARD_KEY
from raidar.grading.records import (
    BundleRef,
    GraderRef,
    SnapshotRef,
    build_step_record,
    load_pre_agent_leak,
    parse_errata,
    step_record_dir,
    stored_snapshot_path,
    write_step_evidence,
    write_step_record,
)
from raidar.grading.sandbox import DockerCopySandbox, GradingSandbox

SnapshotSource = Literal["artifact", "download"]


class SnapshotUnavailableError(RuntimeError):
    """The protocol's snapshot source gave no usable ``/app``: an infra failure of the step.

    Harbor records it as the step's verifier exception; the trial classifier reads the step
    as ``no_verifier_record:SnapshotUnavailableError``, never as a graded verdict.
    """


LEAK_REWARD_KEY = "oracle_leak"


def step_rewards(rewards: dict[str, float], *, leaked: bool) -> dict[str, float]:
    """A leaked oracle invalidates the step: no verdict or rate is reported.

    The keys are omitted rather than zeroed (F6); ``solved`` is therefore
    missing, which Harbor's ``min_reward`` treats as a halt.
    """
    if not leaked:
        return rewards | {LEAK_REWARD_KEY: 0.0}
    return {INFRA_REWARD_KEY: rewards[INFRA_REWARD_KEY], LEAK_REWARD_KEY: 1.0}


ARTIFACT_MANIFEST = "manifest.json"


def _artifact_status(app_dir: Path) -> str | None:
    """Harbor's recorded status for the /app artifact at ``app_dir``, if any."""
    manifest = app_dir.parent / ARTIFACT_MANIFEST
    if not manifest.is_file():
        return None
    try:
        entries = json.loads(manifest.read_text())
    except (OSError, ValueError):
        return "manifest_unreadable"
    if isinstance(entries, dict):
        entries = entries.get("entries", [])
    statuses = [
        str(entry.get("status"))
        for entry in entries
        if isinstance(entry, dict)
        if entry.get("source") == APP_SOURCE
        and str(entry.get("destination", "")).endswith(f"/{APP_ARTIFACT_DESTINATION}")
    ]
    return statuses[-1] if statuses else None


class RaidarVerifier(BaseVerifier):
    def __init__(
        self,
        *,
        oracle_bundle: str,
        docker_context: str,
        snapshot_source: SnapshotSource = "artifact",
        coverage: bool = True,
        grader_timeout_s: float = DEFAULT_RUN_TIMEOUT_S,
        work_root: str | None = None,
        require_pre_agent_check: bool = True,
        errata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if snapshot_source not in ("artifact", "download"):
            raise ValueError(f"snapshot_source must be artifact or download, not {snapshot_source}")
        self.bundle: VerifierBundle = load_bundle(Path(oracle_bundle))
        self.docker_context = docker_context
        self.snapshot_source: SnapshotSource = snapshot_source
        self.coverage = coverage
        self.grader_timeout_s = grader_timeout_s
        self.work_root = Path(work_root) if work_root else None
        self.require_pre_agent_check = require_pre_agent_check
        self.errata = parse_errata(errata)

    # --------------------------------------------------------------- hooks
    async def prepare_sandbox(self) -> tuple[GradingSandbox, GraderRef]:
        image = await ensure_grader_image(
            self.bundle.manifest.grader, docker_context=self.docker_context
        )
        sandbox = DockerCopySandbox(
            image=image.tag,
            docker_context=self.docker_context,
            # The run-scoped environment names its job directory's scope (a label for sweeps).
            run_scope=getattr(self.environment, "run_scope", ""),
        )
        environment_id = await sandbox.environment_id()
        return sandbox, GraderRef(
            image=image.tag,
            environment_id=environment_id,
            image_built=image.built,
            timeout_s=self.grader_timeout_s,
        )

    def _pre_agent_leak(self, step: str, record_dir: Path) -> LeakReport | None:
        report = load_pre_agent_leak(record_dir)
        if report is None and self.require_pre_agent_check:
            raise LeakScanError(
                f"no pre-agent leak check for step {step!r}: the trial runner must call "
                "raidar.grading.guard.install_leak_guard(trial, oracle_bundle)"
            )
        return report

    def _artifact_snapshot(self) -> Path:
        """The collected /app artifact; raises ``SnapshotUnavailableError`` when unusable.

        Harbor creates the target directory before downloading and leaves it
        behind when the download fails or stops partway, recording the failure
        only in ``manifest.json``. A directory whose manifest entry is not
        ``ok`` is therefore an empty or truncated tree, never the snapshot.
        """
        artifacts = (
            self.trial_paths.step_artifacts_dir(self.step_name)
            if self.step_name is not None
            else self.trial_paths.artifacts_dir
        )
        path = artifacts / APP_ARTIFACT_DESTINATION
        status = _artifact_status(path)
        if status is None and path.is_dir():
            return path
        if status == "ok":
            return path
        reason = "artifact_missing" if status is None else f"artifact_{status}"
        raise SnapshotUnavailableError(
            f"step {self.step_name!r}: the /app artifact at {path} is unusable ({reason}); "
            "snapshot_source=artifact never falls back to a download"
        )

    async def _snapshot(self, work: Path, record_dir: Path) -> tuple[Path, SnapshotRef]:
        if self.snapshot_source == "artifact":
            artifact = self._artifact_snapshot()
            ref = SnapshotRef(
                requested="artifact",
                used="artifact",
                digest=tree_digest(artifact),
                stored_path=stored_snapshot_path(self.step_name),
            )
            return artifact, ref
        downloaded = work / "download"
        await self.environment.download_dir_with_exclusions(
            source_dir=APP_SOURCE, target_dir=downloaded, exclude=list(SNAPSHOT_EXCLUDES)
        )
        # Keep the downloaded snapshot host-side so the step can be regraded.
        kept = record_dir / "snapshot"
        shutil.rmtree(kept, ignore_errors=True)
        shutil.copytree(downloaded, kept, symlinks=True)
        ref = SnapshotRef(
            requested="download",
            used="download",
            digest=tree_digest(downloaded),
            stored_path=kept.relative_to(self.trial_paths.trial_dir).as_posix(),
        )
        return downloaded, ref

    # -------------------------------------------------------------- verify
    async def verify(self) -> VerifierResult:
        started = time.perf_counter()
        step = self.bundle.resolve_step(self.step_name)
        record_dir = step_record_dir(self.trial_paths.trial_dir, step)
        timings: dict[str, float] = {}

        pre_agent = self._pre_agent_leak(step, record_dir)
        leak = await scan_agent_environment(self.environment, self.bundle)
        timings["leak_check_s"] = leak.seconds

        mark = time.perf_counter()
        sandbox, grader = await self.prepare_sandbox()
        timings["grader_image_s"] = time.perf_counter() - mark

        work = Path(tempfile.mkdtemp(prefix=f"raidar-oracle-{step}-", dir=self.work_root))
        try:
            mark = time.perf_counter()
            snapshot, snapshot_ref = await self._snapshot(work, record_dir)
            timings["snapshot_transfer_s"] = time.perf_counter() - mark
            result = await grade_step(
                self.bundle,
                step,
                snapshot,
                sandbox,
                work / "runs",
                coverage=self.coverage,
                timeout_s=self.grader_timeout_s,
            )
            timings.update(result.timings)
            mark = time.perf_counter()
            write_step_evidence(record_dir, result)
            _keep_logs(work / "runs", record_dir)
            evidence_write_s = time.perf_counter() - mark
        finally:
            shutil.rmtree(work, ignore_errors=True)  # cleanup: counted as plumbing
        leaked = not leak.clean or (pre_agent is not None and not pre_agent.clean)
        rewards = step_rewards(result.grade.rewards(), leaked=leaked)
        record = build_step_record(
            result,
            bundle=BundleRef(
                root=str(self.bundle.root),
                digest=self.bundle.digest(),
                problem=self.bundle.manifest.problem,
            ),
            snapshot=snapshot_ref,
            grader=grader,
            coverage_enabled=self.coverage,
            leaks=(leak, pre_agent),
            rewards=rewards,
            timings=timings,
            errata=self.errata,
        )
        mark = time.perf_counter()
        write_step_record(record_dir, record)
        # result_write_s (evidence, logs, record.json) is a component of plumbing, as in
        # the N1 row "verifier plumbing"; staged-copy cleanup is plumbing too. The
        # record is written once to measure it, then rewritten with final timings.
        timings["result_write_s"] = evidence_write_s + time.perf_counter() - mark
        timings["verifier_total_s"] = time.perf_counter() - started
        timings["verifier_plumbing_s"] = _plumbing(timings)
        write_step_record(record_dir, record.model_copy(update={"timings": dict(timings)}))
        return VerifierResult(rewards=rewards)


def _keep_logs(runs: Path, record_dir: Path) -> None:
    for role in ("grading", "coverage"):
        log = runs / role / "out" / "sandbox.log"
        if log.is_file():
            shutil.copy2(log, record_dir / f"{role}.log")


def _plumbing(timings: dict[str, float]) -> float:
    """Verifier wall time not spent in a separately budgeted phase (N1).

    Includes result handling (``result_write_s``) and staged-copy cleanup.
    """
    measured = sum(
        timings.get(key, 0.0)
        for key in (
            "leak_check_s",
            "grader_image_s",
            "snapshot_transfer_s",
            "oracle_run_s",
            "coverage_run_s",
        )
    )
    return max(timings["verifier_total_s"] - measured, 0.0)
