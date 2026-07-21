"""Task-image preparation for a rendered workspace bundle."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from raidar.runtime import task_images
from raidar.runtime.execution_contract import EffectiveRunContract
from raidar.runtime.models import RunRequest
from raidar.runtime.task_bundle_images import task_image_reference


@dataclass(frozen=True, slots=True)
class TaskImagePreparationResult:
    """Cache metadata and timings produced by task-image preparation."""

    cache_metadata: dict[str, object]
    timings: dict[str, float]


def prepare_task_image(
    *,
    request: RunRequest,
    contract: EffectiveRunContract,
    task_bundle_path: Path,
    run_env: dict[str, str],
    harbor_dir: Path,
    cache_metadata: dict[str, object],
    timings: dict[str, float],
) -> TaskImagePreparationResult:
    """Resolve, maintain, and ensure the reusable task image for one run."""

    updated_metadata = dict(cache_metadata)
    updated_metadata["contract"] = {
        "id": contract.id,
        "hash": contract.contract_hash,
        "cache_payload": contract.cache_payload,
    }
    image_ref = task_image_reference(request, task_bundle_path, contract)
    if image_ref:
        updated_metadata["image_key"] = image_ref.cache_key
        updated_metadata["image_tag"] = image_ref.tag
        updated_metadata["judge_image_name"] = f"{image_ref.image_name}-reserve"
    task_images._maybe_run_cache_maintenance(
        run_env=run_env,
        active_image_name=image_ref.image_name if image_ref else None,
        runtime_profile=contract.runtime_profile,
    )

    phase_started = time.perf_counter()
    image_hit = None
    if image_ref:
        image_hit = task_images._ensure_task_image(
            task_images.TaskImageEnsureRequest(
                task_bundle_path=task_bundle_path,
                image_ref=image_ref,
                harness=request.config.harness.value,
                run_env=run_env,
                log_dir=harbor_dir,
                task_timeout_sec=request.config.timeout_sec,
                provided_capabilities=contract.provided_capabilities,
                required_capabilities=contract.required_capabilities,
                runtime_profile=contract.runtime_profile,
            )
        )
    updated_timings = {
        **timings,
        "_ensure_task_image": round(time.perf_counter() - phase_started, 3),
    }
    updated_metadata["image"] = {"hit": image_hit}
    return TaskImagePreparationResult(
        cache_metadata=updated_metadata,
        timings=updated_timings,
    )
