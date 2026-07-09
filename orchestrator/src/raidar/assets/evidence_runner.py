"""Bundled evidence action runner.

The runner consumes a resolved verification plan and writes evidence artifacts
without applying scorecard policy or metric scoring.
"""

from __future__ import annotations

import argparse
import glob as globlib
import hashlib
import json
import os
import re
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any

try:
    from raidar.sanitization import sanitize_evidence_payload, sanitize_persisted_text
except ModuleNotFoundError:  # pragma: no cover - bundled asset fallback

    def sanitize_persisted_text(text: str, *, max_chars: int = 4000) -> str:
        return text.strip()[:max_chars]

    def sanitize_evidence_payload(value: Any, *, max_chars: int = 180) -> Any:
        if isinstance(value, str):
            return value.strip()[:max_chars]
        if isinstance(value, list):
            return [sanitize_evidence_payload(item, max_chars=max_chars) for item in value]
        if isinstance(value, dict):
            return {
                key: sanitize_evidence_payload(item, max_chars=max_chars)
                for key, item in value.items()
            }
        return value


MAX_TEXT_BYTES = 512 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_GLOB_MATCHES = 500
DEFAULT_TIMEOUT_SEC = 60
DEFAULT_OUTPUT_DIR = Path("evidence")
EVIDENCE_FILENAME = "evidence.json"
LOG_DIRNAME = "action-logs"
RUNTIME_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".raidar",
    "coverage",
    "dist",
    "build",
}
SECRET_KEY_PARTS = ("api_key", "apikey", "token", "secret", "password", "passwd", "pwd")
ODIFF_THRESHOLD = "0.03"


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _stable_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _mask_secret_value(value: Any) -> str:
    text = str(value)
    if len(text) <= 12:
        return "<redacted>"
    return f"{text[:4]}...{text[-4:]}"


def _secret_shaped_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SECRET_KEY_PARTS)


def _sanitize_json_evidence(value: Any, *, parent_key: str | None = None) -> Any:
    if parent_key and _secret_shaped_key(parent_key):
        return _mask_secret_value(value)
    if isinstance(value, dict):
        return {
            key: _sanitize_json_evidence(item, parent_key=str(key)) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_json_evidence(item, parent_key=parent_key) for item in value]
    return sanitize_evidence_payload(value, max_chars=MAX_TEXT_BYTES)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_stable_json(payload), encoding="utf-8")


def _resolved_path(workspace: Path, value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = workspace / path
    resolved = path.resolve()
    workspace_resolved = workspace.resolve()
    try:
        resolved.relative_to(workspace_resolved)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {value}") from exc
    return resolved


def _relative_path(workspace: Path, path: Path) -> str:
    return path.resolve().relative_to(workspace.resolve()).as_posix()


def _action_id(action: dict[str, Any], index: int) -> str:
    raw = action.get("id") or action.get("name") or f"action-{index + 1}"
    return str(raw)


def _actions_from_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = (
        plan.get("actions"),
        plan.get("evidence_actions"),
        plan.get("verification_actions"),
        plan.get("evidence", {}).get("actions") if isinstance(plan.get("evidence"), dict) else None,
    )
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _declared_artifacts_from_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = (
        plan.get("artifacts"),
        plan.get("declared_artifacts"),
        plan.get("evidence", {}).get("artifacts")
        if isinstance(plan.get("evidence"), dict)
        else None,
    )
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _workspace_from_plan(plan: dict[str, Any], plan_path: Path) -> Path:
    raw = plan.get("workspace") or plan.get("workspace_dir") or plan.get("app_dir")
    if raw:
        path = Path(str(raw))
        if not path.is_absolute():
            path = plan_path.parent / path
        return path.resolve()
    return Path.cwd().resolve()


def _output_dir_from_plan(plan: dict[str, Any], explicit_output_dir: Path | None) -> Path:
    if explicit_output_dir is not None:
        return explicit_output_dir.resolve()
    raw = plan.get("output_dir") or plan.get("evidence_dir")
    return Path(str(raw)).resolve() if raw else (Path.cwd() / DEFAULT_OUTPUT_DIR).resolve()


def _truncate_bytes(data: bytes, max_bytes: int) -> tuple[bytes, bool]:
    if len(data) <= max_bytes:
        return data, False
    return data[:max_bytes], True


def _read_text_file(path: Path, max_bytes: int) -> dict[str, Any]:
    data = path.read_bytes()
    chunk, truncated = _truncate_bytes(data, max_bytes)
    return {
        "bytes": len(data),
        "content": sanitize_persisted_text(
            chunk.decode("utf-8", errors="replace"),
            max_chars=max_bytes,
        ),
        "sha256": hashlib.sha256(data).hexdigest(),
        "truncated": truncated,
    }


def _runtime_excluded(path: Path) -> bool:
    return any(part in RUNTIME_DIR_NAMES for part in path.parts)


def _run_command(action: dict[str, Any], workspace: Path) -> dict[str, Any]:
    config = _action_config(action)
    command = config.get("argv") or action.get("command")
    if not isinstance(command, str | list) or command == []:
        raise ValueError("command action requires config.argv")
    timeout_sec = float(action.get("timeout_sec", DEFAULT_TIMEOUT_SEC))
    cwd = _resolved_path(workspace, str(config.get("cwd") or action.get("cwd", ".")))
    started = time.monotonic()
    return _command_evidence(command, cwd, workspace, timeout_sec, started)


def _command_evidence(
    command: str | list[Any],
    cwd: Path,
    workspace: Path,
    timeout_sec: float,
    started: float,
) -> dict[str, Any]:
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            shell=isinstance(command, str),
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        stdout = exc.stdout or ""
        stderr = exc.stderr or f"Timed out after {timeout_sec:g}s"
    except FileNotFoundError as exc:
        exit_code = 127
        stdout = ""
        stderr = str(exc)
    duration_sec = round(time.monotonic() - started, 3)
    return _command_payload(
        command=command,
        cwd=cwd,
        workspace=workspace,
        duration_sec=duration_sec,
        exit_code=exit_code,
        stderr=str(stderr),
        stdout=str(stdout),
        timed_out=timed_out,
    )


def _command_payload(
    *,
    command: str | list[Any],
    cwd: Path,
    workspace: Path,
    duration_sec: float,
    exit_code: int,
    stderr: str,
    stdout: str,
    timed_out: bool,
) -> dict[str, Any]:
    return {
        "command": command if isinstance(command, str) else [str(part) for part in command],
        "cwd": _relative_path(workspace, cwd),
        "duration_sec": duration_sec,
        "exit_code": exit_code,
        "stderr": sanitize_persisted_text(stderr, max_chars=MAX_TEXT_BYTES),
        "stdout": sanitize_persisted_text(stdout, max_chars=MAX_TEXT_BYTES),
        "timed_out": timed_out,
    }


def _run_read_file(action: dict[str, Any], workspace: Path) -> dict[str, Any]:
    config = _action_config(action)
    path = _resolved_path(workspace, str(config.get("path") or action.get("path", "")))
    max_bytes = int(config.get("max_bytes") or action.get("max_bytes", MAX_TEXT_BYTES))
    result = _read_text_file(path, max_bytes=max(0, min(max_bytes, MAX_TEXT_BYTES)))
    return {"path": _relative_path(workspace, path), **result}


def _run_read_json(action: dict[str, Any], workspace: Path) -> dict[str, Any]:
    config = _action_config(action)
    path = _resolved_path(workspace, str(config.get("path") or action.get("path", "")))
    max_bytes = int(config.get("max_bytes") or action.get("max_bytes", MAX_JSON_BYTES))
    data = path.read_bytes()
    chunk, truncated = _truncate_bytes(data, max(0, min(max_bytes, MAX_JSON_BYTES)))
    parsed = json.loads(chunk.decode("utf-8", errors="replace"))
    return {
        "bytes": len(data),
        "content": _sanitize_json_evidence(parsed),
        "path": _relative_path(workspace, path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "truncated": truncated,
    }


def _run_glob(action: dict[str, Any], workspace: Path) -> dict[str, Any]:
    config = _action_config(action)
    pattern = str(config.get("pattern") or action.get("pattern", ""))
    if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
        raise ValueError("glob pattern must be workspace-relative")
    max_matches = int(action.get("max_matches", MAX_GLOB_MATCHES))
    matches = []
    for raw in globlib.iglob(str(workspace / pattern), recursive=True):
        path = Path(raw)
        rel = _relative_path(workspace, path)
        if _runtime_excluded(Path(rel)):
            continue
        matches.append(rel)
    matches = sorted(set(matches))[: max(0, min(max_matches, MAX_GLOB_MATCHES))]
    return {"matches": matches, "pattern": pattern}


def _run_visual_diff(action: dict[str, Any], workspace: Path) -> dict[str, Any]:
    config = _action_config(action)
    reference = _visual_path(action, config, workspace, ("reference", "expected"))
    actual = _visual_path(action, config, workspace, ("actual",))
    diff = _visual_path(action, config, workspace, ("diff",))
    threshold = str(
        config.get("odiff_threshold") or action.get("odiff_threshold") or ODIFF_THRESHOLD
    )
    regions = _visual_regions(config.get("regions") or action.get("regions") or [])

    result = _visual_diff_base_result(workspace, actual, reference, diff, threshold, regions)
    if reference.exists() and actual.exists():
        result["global"] = _run_odiff(reference, actual, diff, workspace, threshold)
        result["diff"] = _path_presence(workspace, diff)
    result["regions"] = [
        _compare_visual_region(region, workspace, threshold)
        for region in _normalized_weight_regions(regions)
    ]
    result["available_region_count"] = sum(
        1 for region in result["regions"] if region.get("comparison") is not None
    )
    return result


def _visual_path(
    action: dict[str, Any],
    config: dict[str, Any],
    workspace: Path,
    keys: tuple[str, ...],
) -> Path:
    for key in keys:
        value = config.get(key) or action.get(key)
        if value is not None:
            return _resolved_path(workspace, str(value))
    return _resolved_path(workspace, "")


def _visual_diff_base_result(
    workspace: Path,
    actual: Path,
    reference: Path,
    diff: Path,
    threshold: str,
    regions: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "actual": _path_presence(workspace, actual),
        "reference": _path_presence(workspace, reference),
        "diff": _path_presence(workspace, diff),
        "odiff_threshold": threshold,
        "capture_succeeded": actual.exists(),
        "global": None,
        "regions": [],
        "expected_region_count": len(regions),
        "available_region_count": 0,
    }


def _path_presence(workspace: Path, path: Path) -> dict[str, Any]:
    return {
        "exists": path.exists(),
        "path": _relative_path(workspace, path),
    }


def _visual_regions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _normalized_weight_regions(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = [region for region in regions if _valid_visual_region(region)]
    total = sum(float(region["weight"]) for region in valid)
    if total <= 0:
        return [{**region, "normalized_weight": 0.0} for region in valid]
    return [{**region, "normalized_weight": float(region["weight"]) / total} for region in valid]


def _region_path_keys() -> tuple[str, str, str]:
    return ("reference_image", "actual_image", "diff_image")


def _valid_visual_region(region: dict[str, Any]) -> bool:
    has_paths = all(
        isinstance(region.get(key), str) and region.get(key) for key in _region_path_keys()
    )
    weight = region.get("weight")
    return has_paths and isinstance(weight, int | float) and float(weight) > 0


def _compare_visual_region(
    region: dict[str, Any], workspace: Path, threshold: str
) -> dict[str, Any]:
    reference = _resolved_path(workspace, str(region["reference_image"]))
    actual = _resolved_path(workspace, str(region["actual_image"]))
    diff = _resolved_path(workspace, str(region["diff_image"]))
    comparison = None
    if reference.exists() and actual.exists():
        comparison = _run_odiff(reference, actual, diff, workspace, threshold)
    return {
        "name": str(region.get("name", "")),
        "weight": float(region.get("weight", 0)),
        "normalized_weight": float(region.get("normalized_weight", 0)),
        "actual": _path_presence(workspace, actual),
        "reference": _path_presence(workspace, reference),
        "diff": _path_presence(workspace, diff),
        "comparison": comparison,
    }


def _run_odiff(
    reference: Path,
    actual: Path,
    diff: Path,
    workspace: Path,
    threshold: str,
) -> dict[str, Any]:
    diff.parent.mkdir(parents=True, exist_ok=True)
    command = ["bunx", "odiff", str(reference), str(actual), str(diff), "--threshold", threshold]
    completed = subprocess.run(
        command,
        cwd=workspace,
        text=True,
        capture_output=True,
        timeout=DEFAULT_TIMEOUT_SEC,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}".strip()
    similarity = 1.0 if completed.returncode == 0 else _similarity_from_odiff_output(output)
    return {
        "command": command,
        "exit_code": completed.returncode,
        "similarity": similarity,
        "raw_output": sanitize_persisted_text(output, max_chars=4000),
        "diff_path": _relative_path(workspace, diff) if diff.exists() else None,
    }


def _similarity_from_odiff_output(output: str) -> float:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", output)
    if not match:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (float(match.group(1)) / 100.0)))


def _run_archive(action: dict[str, Any], workspace: Path, output_dir: Path) -> dict[str, Any]:
    config = _action_config(action)
    archive_path = output_dir / _archive_name(action, config)
    paths = _archive_source_paths(action, config)
    if not isinstance(paths, list):
        raise ValueError("archive action requires paths list")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archived: list[str] = []
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in _archive_files(paths, workspace):
            rel = _relative_path(workspace, path)
            archive.add(path, arcname=rel)
            archived.append(rel)
    return {
        "archive": str(archive_path),
        "archived": archived,
        "exists": archive_path.exists(),
    }


def _archive_name(action: dict[str, Any], config: dict[str, Any]) -> str:
    return str(
        config.get("archive")
        or action.get("archive")
        or action.get("output")
        or "evidence-artifacts.tar.gz"
    )


def _archive_source_paths(action: dict[str, Any], config: dict[str, Any]) -> list[Any]:
    paths = config.get("paths") or action.get("paths") or action.get("include") or []
    if paths:
        return paths
    return [config["path"]] if config.get("path") else []


def _archive_files(paths: list[Any], workspace: Path) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = _resolved_path(workspace, str(raw))
        if not path.exists():
            continue
        if path.is_file() and not _runtime_excluded(Path(_relative_path(workspace, path))):
            files.append(path)
        if path.is_dir():
            files.extend(_archive_dir_files(path, workspace))
    return sorted(files)


def _archive_dir_files(path: Path, workspace: Path) -> list[Path]:
    return [
        child
        for child in path.rglob("*")
        if child.is_file() and not _runtime_excluded(Path(_relative_path(workspace, child)))
    ]


def _execute_action(action: dict[str, Any], workspace: Path, output_dir: Path) -> dict[str, Any]:
    kind = action.get("kind") or action.get("type")
    if kind == "command":
        return _run_command(action, workspace)
    if kind == "read_file":
        return _run_read_file(action, workspace)
    if kind == "read_json":
        return _run_read_json(action, workspace)
    if kind == "glob":
        return _run_glob(action, workspace)
    if kind == "visual_diff":
        return _run_visual_diff(action, workspace)
    if kind == "archive":
        return _run_archive(action, workspace, output_dir)
    raise ValueError(f"unsupported evidence action kind: {kind}")


def _action_config(action: dict[str, Any]) -> dict[str, Any]:
    config = action.get("config")
    return config if isinstance(config, dict) else {}


def run_evidence_plan(
    plan_path: Path,
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Run raw evidence actions from a resolved plan and persist evidence.json."""

    plan = _read_json(plan_path)
    if not isinstance(plan, dict):
        raise ValueError("verification plan must be a JSON object")
    workspace = _workspace_from_plan(plan, plan_path)
    resolved_output_dir = _output_dir_from_plan(plan, output_dir)
    actions = _actions_from_plan(plan)
    action_results = []
    logs_dir = resolved_output_dir / LOG_DIRNAME

    for index, action in enumerate(actions):
        identifier = _action_id(action, index)
        record = _action_record(action, identifier, workspace, resolved_output_dir)
        action_results.append(record)
        _write_json(logs_dir / f"{index + 1:03d}-{identifier}.json", record)

    evidence = {
        "actions": action_results,
        "artifacts": _declared_artifacts_from_plan(plan),
        "metadata": {
            "action_count": len(action_results),
            "error_count": sum(1 for item in action_results if item["status"] == "error"),
            "required_error_count": sum(
                1 for item in action_results if item["required"] and item["status"] == "error"
            ),
            "workspace": str(workspace),
        },
    }
    _write_json(resolved_output_dir / EVIDENCE_FILENAME, evidence)
    return evidence


def _action_record(
    action: dict[str, Any],
    identifier: str,
    workspace: Path,
    output_dir: Path,
) -> dict[str, Any]:
    started = time.monotonic()
    record: dict[str, Any] = {
        "id": identifier,
        "kind": str(action.get("kind") or action.get("type") or ""),
        "required": bool(action.get("required", False)),
        "status": "ok",
    }
    try:
        record["result"] = _execute_action(action, workspace, output_dir)
    except Exception as exc:  # noqa: BLE001 - action errors are evidence
        record["status"] = "error"
        record["error"] = _action_error(exc)
    record["duration_sec"] = round(time.monotonic() - started, 3)
    return record


def _action_error(exc: Exception) -> dict[str, str]:
    return {
        "message": sanitize_persisted_text(str(exc), max_chars=1000),
        "type": exc.__class__.__name__,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run raw evidence actions from verification-plan.json"
    )
    parser.add_argument("plan", type=Path, help="Resolved verification-plan.json")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    run_evidence_plan(args.plan, output_dir=args.output_dir)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
