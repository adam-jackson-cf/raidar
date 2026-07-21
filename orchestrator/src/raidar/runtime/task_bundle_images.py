"""Task image identity derived from a rendered Harbor bundle."""

from __future__ import annotations

from pathlib import Path

from raidar.agents.harbor_routing import is_task_image_reuse_enabled, task_image_prefix
from raidar.audit.workspace_diff import directory_fingerprint
from raidar.runtime.execution_contract import EffectiveRunContract
from raidar.runtime.identifiers import slug_fragment
from raidar.runtime.models import RunRequest, TaskImageRef
from raidar.runtime.workspace_cache import RAIDAR_CACHE_VERSION, _hash_json_payload


def task_image_reference(
    request: RunRequest,
    task_bundle_path: Path,
    contract: EffectiveRunContract,
) -> TaskImageRef | None:
    """Return the reusable task-image identity for a completed bundle."""

    if not is_task_image_reuse_enabled():
        return None
    environment_dir = task_bundle_path / "environment"
    dockerfile_path = environment_dir / "Dockerfile"
    app_dir = environment_dir / "app"
    tests_dir = task_bundle_path / "tests"
    if not dockerfile_path.exists() or not app_dir.exists():
        return None

    payload = {
        "cache_version": RAIDAR_CACHE_VERSION,
        "effective_run_contract": contract.cache_payload,
        "effective_run_contract_hash": contract.contract_hash,
        "dockerfile": dockerfile_path.read_text(encoding="utf-8"),
        "app_fingerprint": directory_fingerprint(app_dir),
        "tests_fingerprint": directory_fingerprint(tests_dir) if tests_dir.exists() else None,
    }
    cache_key = _hash_json_payload(payload)
    digest = cache_key[:16]
    harness_fragment = slug_fragment(request.config.harness.value)
    image_tag = f"task-env-{harness_fragment}-{digest}"
    return TaskImageRef(
        image_name=f"{task_image_prefix()}:{image_tag}",
        cache_key=cache_key,
        tag=image_tag,
    )
