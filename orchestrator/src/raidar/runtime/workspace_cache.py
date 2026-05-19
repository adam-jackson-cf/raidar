"""Workspace cache roots, locks, and baseline cache services."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from raidar.agents.config import Harness
from raidar.agents.rules import SYSTEM_RULES
from raidar.audit.workspace_diff import directory_fingerprint
from raidar.runtime.models import BaselineWorkspaceCacheResult, RunRequest
from raidar.runtime.wait import wait_for_cache_lock_retry
from raidar.schemas.scenario import ScenarioDefinition

_SUITE_BASELINE_LOCKS_GUARD = threading.Lock()

_SUITE_BASELINE_LOCKS: dict[Path, threading.Lock] = {}

RAIDAR_CACHE_VERSION = "1"

RAIDAR_CACHE_LOCK_TIMEOUT_SEC = 10 * 60

RAIDAR_CACHE_LOCK_STALE_SEC = 60 * 60


def _harness_value(harness: Harness | Any) -> str:
    return str(getattr(harness, "value", harness))


@dataclass(frozen=True, slots=True)
class BaselineWorkspaceRequest:
    """Input for preparing or reusing a cached baseline workspace."""

    scenario: ScenarioDefinition
    starter_dir: Path
    baseline_workspace_dir: Path
    baseline_cache_key: str
    scenario_dir: Path
    harness: Harness


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _raidar_cache_root() -> Path:
    return _repo_root() / ".cache" / "raidar"


def _prep_cache_root() -> Path:
    return _raidar_cache_root() / "prep"


def _baseline_cache_entry_dir(cache_key: str) -> Path:
    return _prep_cache_root() / "baselines" / cache_key


def _baseline_cache_workspace_dir(cache_key: str) -> Path:
    return _baseline_cache_entry_dir(cache_key) / "workspace"


def _preflight_cache_file(cache_key: str) -> Path:
    return _prep_cache_root() / "preflight" / f"{cache_key}.ok.json"


def _cache_lock_root() -> Path:
    return _raidar_cache_root() / "locks"


def _task_image_cache_metadata_path(cache_key: str) -> Path:
    return _raidar_cache_root() / "images" / f"{cache_key}.json"


def _maintenance_marker_path() -> Path:
    return _raidar_cache_root() / "maintenance" / "last-prune.json"


def _repo_cache_identity() -> str:
    digest = hashlib.sha256(str(_repo_root().resolve()).encode("utf-8")).hexdigest()
    return digest[:16]


def _hash_json_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _effective_rule_source(request: RunRequest) -> Path | None:
    injected_rule_name = SYSTEM_RULES.get(request.config.harness)
    if not injected_rule_name:
        return None
    candidate = request.scenario_dir / "rules" / injected_rule_name
    return candidate if candidate.exists() else None


def _injected_rules_hash(request: RunRequest) -> str | None:
    rule_source = _effective_rule_source(request)
    if rule_source is None:
        return None
    return _hash_bytes(rule_source.read_bytes())


def _baseline_cache_key(request: RunRequest, starter_fingerprint: str) -> str:
    payload = {
        "cache_version": RAIDAR_CACHE_VERSION,
        "starter_fingerprint": starter_fingerprint,
        "harness": request.config.harness.value,
        "injected_rules_hash": _injected_rules_hash(request),
        "setup_actions": getattr(request.scenario.verification, "setup_actions", []),
    }
    return _hash_json_payload(payload)


def _touch_cache_path(path: Path) -> None:
    now = time.time()
    try:
        os.utime(path, (now, now))
    except FileNotFoundError:
        return


def _cache_lock_owner_pid(lock_dir: Path) -> int | None:
    owner_path = lock_dir / "owner.json"
    try:
        payload = json.loads(owner_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    pid = payload.get("pid")
    return pid if isinstance(pid, int) and pid > 0 else None


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def _cache_key_lock(lock_key: str, *, timeout_sec: int = RAIDAR_CACHE_LOCK_TIMEOUT_SEC):
    lock_root = _cache_lock_root()
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_dir = lock_root / f"{lock_key}.lock"
    deadline = time.monotonic() + timeout_sec

    while True:
        try:
            lock_dir.mkdir(parents=False, exist_ok=False)
            (lock_dir / "owner.json").write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            break
        except FileExistsError as err:
            try:
                age_sec = time.time() - lock_dir.stat().st_mtime
            except FileNotFoundError:
                continue
            owner_pid = _cache_lock_owner_pid(lock_dir)
            if owner_pid is not None and not _process_exists(owner_pid):
                shutil.rmtree(lock_dir, ignore_errors=True)
                continue
            if age_sec > RAIDAR_CACHE_LOCK_STALE_SEC:
                shutil.rmtree(lock_dir, ignore_errors=True)
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for cache lock `{lock_key}`.") from err
            wait_for_cache_lock_retry()

    try:
        yield
    finally:
        shutil.rmtree(lock_dir, ignore_errors=True)


def _baseline_workspace_lock(baseline_workspace_dir: Path) -> threading.Lock:
    key = baseline_workspace_dir.resolve()
    with _SUITE_BASELINE_LOCKS_GUARD:
        lock = _SUITE_BASELINE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SUITE_BASELINE_LOCKS[key] = lock
        return lock


def _baseline_cache_entry_metadata(request: BaselineWorkspaceRequest) -> dict[str, str] | None:
    metadata_path = request.baseline_workspace_dir.parent / "metadata.json"
    payload = _load_baseline_cache_payload(request.baseline_workspace_dir, metadata_path)
    if payload is None or not _baseline_cache_payload_matches(request, payload):
        return None

    baseline_fingerprint = payload.get("baseline_fingerprint")
    if not isinstance(baseline_fingerprint, str) or not baseline_fingerprint:
        return None
    if baseline_fingerprint != directory_fingerprint(request.baseline_workspace_dir):
        return None
    return {"baseline_fingerprint": baseline_fingerprint}


def _load_baseline_cache_payload(
    baseline_workspace_dir: Path, metadata_path: Path
) -> dict[str, Any] | None:
    if not baseline_workspace_dir.exists() or not metadata_path.exists():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _baseline_cache_payload_matches(
    request: BaselineWorkspaceRequest, payload: dict[str, Any]
) -> bool:
    return payload.get("cache_key") == request.baseline_cache_key and payload.get(
        "harness"
    ) == _harness_value(request.harness)


def _ensure_baseline_workspace(request: BaselineWorkspaceRequest) -> BaselineWorkspaceCacheResult:
    entry_dir = request.baseline_workspace_dir.parent
    metadata_path = entry_dir / "metadata.json"
    lock_key = f"baseline-{request.baseline_cache_key}"
    with _baseline_workspace_lock(request.baseline_workspace_dir), _cache_key_lock(lock_key):
        hit = _baseline_workspace_cache_hit(request, metadata_path, entry_dir)
        if hit is not None:
            return hit
        invalidated = entry_dir.exists()
        if entry_dir.exists():
            shutil.rmtree(entry_dir, ignore_errors=True)
        entry_dir.mkdir(parents=True, exist_ok=True)
        try:
            return _create_baseline_workspace(request, metadata_path, entry_dir, invalidated)
        except Exception:
            shutil.rmtree(entry_dir, ignore_errors=True)
            raise


def _baseline_workspace_cache_hit(
    request: BaselineWorkspaceRequest,
    metadata_path: Path,
    entry_dir: Path,
) -> BaselineWorkspaceCacheResult | None:
    entry_metadata = _baseline_cache_entry_metadata(request)
    if entry_metadata is None:
        return None
    _touch_cache_path(entry_dir)
    return BaselineWorkspaceCacheResult(
        metadata_path=metadata_path,
        baseline_fingerprint=entry_metadata["baseline_fingerprint"],
        hit=True,
        status="hit",
    )


def _create_baseline_workspace(
    request: BaselineWorkspaceRequest,
    metadata_path: Path,
    entry_dir: Path,
    invalidated: bool,
) -> BaselineWorkspaceCacheResult:
    from raidar.runtime.starter_preflight import _run_workspace_setup_actions
    from raidar.runtime.workspace import _workspace_runtime_env, prepare_workspace

    prepare_workspace(
        starter_dir=request.starter_dir,
        target_dir=request.baseline_workspace_dir,
        scenario_dir=request.scenario_dir,
        harness=request.harness,
    )
    _run_workspace_setup_actions(
        workspace=request.baseline_workspace_dir,
        env=_workspace_runtime_env(request.baseline_workspace_dir, os.environ.copy()),
        setup_actions=request.scenario.verification.setup_actions,
    )
    baseline_fingerprint = directory_fingerprint(request.baseline_workspace_dir)
    _write_baseline_workspace_metadata(request, metadata_path, baseline_fingerprint)
    _touch_cache_path(entry_dir)
    return BaselineWorkspaceCacheResult(
        metadata_path=metadata_path,
        baseline_fingerprint=baseline_fingerprint,
        hit=False,
        status="invalidated" if invalidated else "miss",
    )


def _write_baseline_workspace_metadata(
    request: BaselineWorkspaceRequest,
    metadata_path: Path,
    baseline_fingerprint: str,
) -> None:
    metadata_path.write_text(
        json.dumps(
            {
                "cache_key": request.baseline_cache_key,
                "baseline_fingerprint": baseline_fingerprint,
                "created_at": datetime.now(UTC).isoformat(),
                "harness": _harness_value(request.harness),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _directory_size_bytes(path: Path) -> int:
    total = 0
    for candidate in path.rglob("*"):
        if candidate.is_file():
            total += candidate.stat().st_size
    return total


def _hash_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
