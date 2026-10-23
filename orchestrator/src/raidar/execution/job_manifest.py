"""The files a job directory holds before its first trial runs, and ``job.json``'s opening fields.

``start_job`` writes ``plan.json``, ``job.lock.json``, the assessment config and the first
``job.json`` (provenance: runner, recipes, effective limits, execution target, admission);
``raidar.execution.run`` completes ``job.json`` once the trials have finished.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from raidar.core.job_layout import JOB_KIND, JOB_LOCK_FILE, JOB_MANIFEST, PLAN_FILE
from raidar.execution.assessment import lock_errata, write_assessment_config
from raidar.execution.health.provenance import effective_limits, recipe_digests, runner_provenance
from raidar.execution.job_context import JobContext


def write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def start_job(ctx: JobContext) -> dict[str, object]:
    """Write the job's opening files into its (created) directory; returns the manifest."""
    plan = ctx.plan
    write_json(ctx.job_dir / PLAN_FILE, plan.to_json())
    if plan.lock:
        write_json(ctx.job_dir / JOB_LOCK_FILE, plan.lock)
    write_assessment_config(
        plan.loaded.spec.assessment,
        plan.loaded.spec.protocol,
        ctx.job_dir,
        lock_errata(plan.lock),
    )
    manifest: dict[str, object] = {
        "kind": JOB_KIND,
        "experiment": plan.experiment_id,
        "job_id": ctx.job_id,
        "lock_sha256": plan.lock_sha256,
        "experiment_file": str(plan.loaded.path),
        **ctx.target.manifest_fields(plan.concurrency),
        "runner": runner_provenance(),
        "recipes": recipe_digests(plan, ctx.problems),
        "effective_limits": effective_limits(plan),
        "concurrency": plan.concurrency,
        **({"admission": dict(ctx.admission)} if ctx.admission else {}),
        "selected": [trial.trial_id for trial in plan.selected_trials()],
        "partial": len(plan.selected) != len(plan.trials),
        "started_at": datetime.now(UTC).isoformat(),
    }
    write_json(ctx.job_dir / JOB_MANIFEST, manifest)
    return manifest
