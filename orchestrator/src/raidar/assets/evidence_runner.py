"""Bundled evidence action runner.

The runner consumes a resolved verification plan and writes evidence artifacts
without applying scorecard policy or metric scoring.
"""

from __future__ import annotations

import ast
import argparse
import fnmatch
import glob as globlib
import hashlib
import json
import os
import re
import subprocess
import tarfile
import time
from dataclasses import dataclass
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


EVIDENCE_FILENAME = "evidence.json"
LOG_DIRNAME = "action-logs"
SCHEMA_VERSION = "1"
RUNNER_CONTRACT = "python-evidence-runner@2"
ACTION_KINDS = {
    "archive",
    "command",
    "glob",
    "public_contract_snapshot",
    "read_file",
    "read_json",
    "structural_snapshot",
    "visual_diff",
}
SECRET_KEY_PARTS = ("api_key", "apikey", "token", "secret", "password", "passwd", "pwd")


@dataclass(frozen=True, slots=True)
class RunnerPolicy:
    """Resolved policy carried by the effective run contract."""

    file_bytes: int
    text_chars: int
    list_items: int
    list_item_chars: int
    archive_excludes: frozenset[str]


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


def _sanitize_json_evidence(
    value: Any,
    *,
    max_chars: int,
    max_items: int,
    parent_key: str | None = None,
) -> Any:
    if parent_key and _secret_shaped_key(parent_key):
        return _mask_secret_value(value)
    if isinstance(value, dict):
        return {
            key: _sanitize_json_evidence(
                item,
                max_chars=max_chars,
                max_items=max_items,
                parent_key=str(key),
            )
            for key, item in list(value.items())[:max_items]
        }
    if isinstance(value, list):
        return [
            _sanitize_json_evidence(
                item,
                max_chars=max_chars,
                max_items=max_items,
                parent_key=parent_key,
            )
            for item in value[:max_items]
        ]
    return sanitize_evidence_payload(value, max_chars=max_chars)


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


def _validated_plan(payload: Any, plan_path: Path) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("verification plan must be a JSON object")
    expected = {
        "schema_version",
        "runner_contract",
        "workspace",
        "output_dir",
        "actions",
        "aliases",
        "limits",
        "archive_excludes",
    }
    _require_exact_keys(payload, expected, "verification plan")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported evidence schema_version: {payload['schema_version']!r}")
    if payload["runner_contract"] != RUNNER_CONTRACT:
        raise ValueError(f"unsupported evidence runner_contract: {payload['runner_contract']!r}")
    if not isinstance(payload["workspace"], str) or not payload["workspace"]:
        raise ValueError("verification plan workspace must be non-empty text")
    if not isinstance(payload["output_dir"], str) or not payload["output_dir"]:
        raise ValueError("verification plan output_dir must be non-empty text")
    policy = _policy_from_plan(payload)
    _validate_actions(payload["actions"], max_snapshot_files=policy.list_items)
    _validate_aliases(payload["aliases"], payload["actions"])
    workspace = Path(payload["workspace"])
    if not workspace.is_absolute():
        payload = {**payload, "workspace": str((plan_path.parent / workspace).resolve())}
    return payload


def _require_exact_keys(payload: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{label} keys mismatch: missing={missing}, extra={extra}")


def _validate_actions(value: Any, *, max_snapshot_files: int) -> None:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("verification plan actions must be a list of objects")
    identifiers: set[str] = set()
    for action in value:
        identifier = _validate_action_shape(
            action, identifiers, max_snapshot_files=max_snapshot_files
        )
        identifiers.add(identifier)
    for action in value:
        unknown = sorted(set(action["depends_on"]) - identifiers)
        if unknown:
            raise ValueError(
                f"verification action {action['id']} has unknown dependencies: {unknown}"
            )


def _validate_action_shape(
    action: dict[str, Any], identifiers: set[str], *, max_snapshot_files: int
) -> str:
    _require_exact_keys(
        action,
        {"id", "kind", "timeout_sec", "required", "depends_on", "config"},
        "verification action",
    )
    identifier = _validate_action_identity(action, identifiers)
    _validate_action_fields(action, identifier, max_snapshot_files=max_snapshot_files)
    _validate_action_timeout(action, identifier)
    return identifier


def _validate_action_identity(action: dict[str, Any], identifiers: set[str]) -> str:
    identifier = action["id"]
    if not isinstance(identifier, str) or not identifier or identifier in identifiers:
        raise ValueError(f"invalid or duplicate verification action id: {identifier!r}")
    if action["kind"] not in ACTION_KINDS:
        raise ValueError(f"unsupported evidence action kind: {action['kind']!r}")
    return identifier


def _validate_action_fields(
    action: dict[str, Any], identifier: str, *, max_snapshot_files: int
) -> None:
    if not isinstance(action["required"], bool):
        raise ValueError(f"verification action {identifier} required must be boolean")
    if not isinstance(action["depends_on"], list) or not all(
        isinstance(item, str) for item in action["depends_on"]
    ):
        raise ValueError(f"verification action {identifier} depends_on must be text list")
    if not isinstance(action["config"], dict):
        raise ValueError(f"verification action {identifier} config must be an object")
    _validate_action_config(
        identifier,
        action["kind"],
        action["config"],
        max_snapshot_files=max_snapshot_files,
    )


def _validate_action_timeout(action: dict[str, Any], identifier: str) -> None:
    if action["kind"] in {"command", "visual_diff"} and not isinstance(action["timeout_sec"], int):
        raise ValueError(f"verification action {identifier} timeout_sec is required")
    if action["timeout_sec"] is not None and action["timeout_sec"] <= 0:
        raise ValueError(f"verification action {identifier} timeout_sec must be positive")


def _validate_action_config(
    identifier: str, kind: str, config: dict[str, Any], *, max_snapshot_files: int
) -> None:
    required, optional = {
        "archive": ({"path", "output"}, set()),
        "command": ({"argv"}, {"cwd"}),
        "glob": ({"pattern"}, set()),
        "public_contract_snapshot": ({"paths"}, set()),
        "read_file": ({"path"}, {"max_bytes"}),
        "read_json": ({"path"}, {"max_bytes"}),
        "structural_snapshot": ({"paths"}, set()),
        "visual_diff": (
            {"reference", "actual", "diff", "argv_template", "threshold", "regions"},
            set(),
        ),
    }[kind]
    actual = set(config)
    if not required.issubset(actual) or not actual.issubset(required | optional):
        raise ValueError(
            f"evidence action {identifier} config keys mismatch: "
            f"required={sorted(required)}, actual={sorted(actual)}"
        )
    if kind == "command":
        _validate_command_config(identifier, config)
    elif kind == "visual_diff":
        _validate_visual_config(identifier, config)
    elif kind in {"read_file", "read_json"}:
        _validate_read_config(identifier, config)
    elif kind in {"structural_snapshot", "public_contract_snapshot"}:
        _validate_snapshot_config(identifier, config, max_snapshot_files=max_snapshot_files)
    elif kind == "archive":
        _validate_text_fields(identifier, config, ("path", "output"))
    elif kind == "glob":
        _validate_text_fields(identifier, config, ("pattern",))


def _validate_command_config(identifier: str, config: dict[str, Any]) -> None:
    argv = config.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        raise ValueError(f"evidence action {identifier} command argv must be a text list")
    cwd = config.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not cwd):
        raise ValueError(f"evidence action {identifier} command cwd must be non-empty text")


def _validate_read_config(identifier: str, config: dict[str, Any]) -> None:
    if not isinstance(config.get("path"), str) or not config["path"]:
        raise ValueError(f"evidence action {identifier} path must be non-empty text")
    max_bytes = config.get("max_bytes")
    if max_bytes is not None and (
        not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0
    ):
        raise ValueError(f"evidence action {identifier} max_bytes must be a positive integer")


def _validate_snapshot_config(
    identifier: str, config: dict[str, Any], *, max_snapshot_files: int
) -> None:
    paths = config.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(path, str) for path in paths):
        raise ValueError(
            f"evidence action {identifier} snapshot paths must be a non-empty text list"
        )
    if len(paths) != len(set(paths)):
        raise ValueError(f"evidence action {identifier} snapshot paths must be unique")
    if len(paths) > max_snapshot_files:
        raise ValueError(f"evidence action {identifier} snapshot paths exceed evidence list limit")
    for path in paths:
        _validate_workspace_relative_path(identifier, path)


def _validate_workspace_relative_path(identifier: str, value: str) -> None:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"evidence action {identifier} snapshot paths must be workspace-relative")


def _validate_text_fields(identifier: str, config: dict[str, Any], fields: tuple[str, ...]) -> None:
    if not all(isinstance(config.get(field), str) and config[field] for field in fields):
        raise ValueError(
            f"evidence action {identifier} requires non-empty text fields: {', '.join(fields)}"
        )


def _validate_visual_config(identifier: str, config: dict[str, Any]) -> None:
    if not all(
        isinstance(config.get(key), str) and config[key] for key in ("reference", "actual", "diff")
    ):
        raise ValueError(f"evidence action {identifier} visual paths must be non-empty text")
    threshold = config.get("threshold")
    if (
        not isinstance(threshold, int | float)
        or isinstance(threshold, bool)
        or not 0 <= threshold <= 1
    ):
        raise ValueError(f"evidence action {identifier} visual threshold must be 0..1")
    _visual_argv_template(config)
    _visual_regions(config.get("regions"))


def _validate_aliases(value: Any, actions: list[dict[str, Any]]) -> None:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError("verification plan aliases must map text to text")
    action_ids = {action["id"] for action in actions}
    unknown = sorted(set(value.values()) - action_ids)
    if unknown:
        raise ValueError(f"verification plan aliases reference unknown actions: {unknown}")


def _policy_from_plan(plan: dict[str, Any]) -> RunnerPolicy:
    limits = plan.get("limits")
    if not isinstance(limits, dict):
        raise ValueError("verification plan limits must be an object")
    _require_exact_keys(
        limits,
        {"file_bytes", "text_chars", "list_items", "list_item_chars"},
        "verification plan limits",
    )
    if not all(isinstance(value, int) and value > 0 for value in limits.values()):
        raise ValueError("verification plan limits must be positive integers")
    excludes = plan.get("archive_excludes")
    if not isinstance(excludes, list) or not all(
        isinstance(item, str) and item for item in excludes
    ):
        raise ValueError("verification plan archive_excludes must be a text list")
    return RunnerPolicy(
        file_bytes=limits["file_bytes"],
        text_chars=limits["text_chars"],
        list_items=limits["list_items"],
        list_item_chars=limits["list_item_chars"],
        archive_excludes=frozenset(excludes),
    )


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


def _runtime_excluded(path: Path, policy: RunnerPolicy) -> bool:
    return any(
        fnmatch.fnmatch(part, pattern) for part in path.parts for pattern in policy.archive_excludes
    )


def _run_command(action: dict[str, Any], workspace: Path, policy: RunnerPolicy) -> dict[str, Any]:
    config = _action_config(action)
    command = config.get("argv")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) for item in command)
    ):
        raise ValueError("command action requires config.argv")
    timeout_sec = float(action["timeout_sec"])
    cwd = _resolved_path(workspace, str(config.get("cwd", ".")))
    started = time.monotonic()
    return _command_evidence(command, cwd, workspace, timeout_sec, started, policy)


def _command_evidence(
    command: list[str],
    cwd: Path,
    workspace: Path,
    timeout_sec: float,
    started: float,
    policy: RunnerPolicy,
) -> dict[str, Any]:
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            shell=False,
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
        max_chars=policy.text_chars,
    )


def _command_payload(
    *,
    command: list[str],
    cwd: Path,
    workspace: Path,
    duration_sec: float,
    exit_code: int,
    stderr: str,
    stdout: str,
    timed_out: bool,
    max_chars: int,
) -> dict[str, Any]:
    return {
        "command": command,
        "cwd": _relative_path(workspace, cwd),
        "duration_sec": duration_sec,
        "exit_code": exit_code,
        "stderr": sanitize_persisted_text(stderr, max_chars=max_chars),
        "stdout": sanitize_persisted_text(stdout, max_chars=max_chars),
        "timed_out": timed_out,
    }


def _run_read_file(action: dict[str, Any], workspace: Path, policy: RunnerPolicy) -> dict[str, Any]:
    config = _action_config(action)
    path = _resolved_path(workspace, str(config["path"]))
    max_bytes = _bounded_file_bytes(config, policy)
    result = _read_text_file(path, max_bytes=max_bytes)
    return {"path": _relative_path(workspace, path), **result}


def _run_read_json(action: dict[str, Any], workspace: Path, policy: RunnerPolicy) -> dict[str, Any]:
    config = _action_config(action)
    path = _resolved_path(workspace, str(config["path"]))
    max_bytes = _bounded_file_bytes(config, policy)
    data = path.read_bytes()
    chunk, truncated = _truncate_bytes(data, max_bytes)
    parsed = json.loads(chunk.decode("utf-8", errors="replace"))
    return {
        "bytes": len(data),
        "content": _sanitize_json_evidence(
            parsed,
            max_chars=policy.list_item_chars,
            max_items=policy.list_items,
        ),
        "path": _relative_path(workspace, path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "truncated": truncated,
    }


def _bounded_file_bytes(config: dict[str, Any], policy: RunnerPolicy) -> int:
    requested = config.get("max_bytes", policy.file_bytes)
    if not isinstance(requested, int) or isinstance(requested, bool) or requested <= 0:
        raise ValueError("evidence max_bytes must be a positive integer")
    return min(requested, policy.file_bytes)


def _run_structural_snapshot(
    action: dict[str, Any], workspace: Path, policy: RunnerPolicy
) -> dict[str, Any]:
    files = _snapshot_files(action, workspace)
    entries = [_structural_snapshot_file(workspace, path) for path in files]
    return {"digest": _snapshot_digest(entries), "files": entries}


def _run_public_contract_snapshot(
    action: dict[str, Any], workspace: Path, policy: RunnerPolicy
) -> dict[str, Any]:
    files = _snapshot_files(action, workspace)
    entries = [_public_contract_snapshot_file(workspace, path, policy) for path in files]
    return {"digest": _snapshot_digest(entries), "files": entries}


def _snapshot_files(action: dict[str, Any], workspace: Path) -> list[Path]:
    paths = _action_config(action).get("paths")
    if not isinstance(paths, list) or not paths:
        raise ValueError("snapshot action requires config.paths")
    files: list[Path] = []
    for value in paths:
        if not isinstance(value, str):
            raise ValueError("snapshot action paths must be text")
        path = _resolved_path(workspace, value)
        if not path.is_file():
            raise ValueError(f"snapshot selected file is unavailable: {value}")
        files.append(path)
    return files


def _structural_snapshot_file(workspace: Path, path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    lines = data.decode("utf-8", errors="replace").splitlines()
    return {
        "bytes": len(data),
        "code_line_count": sum(
            1 for line in lines if line.strip() and not line.lstrip().startswith(("#", "//"))
        ),
        "line_count": len(lines),
        "nonempty_line_count": sum(1 for line in lines if line.strip()),
        "path": _relative_path(workspace, path),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _public_contract_snapshot_file(
    workspace: Path, path: Path, policy: RunnerPolicy
) -> dict[str, Any]:
    data = path.read_bytes()
    declarations = _public_declarations(path, data)
    if len(declarations) > policy.list_items:
        raise ValueError("public contract declarations exceed evidence list limit")
    if any(len(declaration) > policy.list_item_chars for declaration in declarations):
        raise ValueError("public contract declaration exceeds evidence item limit")
    return {
        "declarations": declarations,
        "path": _relative_path(workspace, path),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _public_declarations(path: Path, data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    if path.suffix == ".py":
        return _python_public_declarations(path, text)
    declarations: list[str] = []
    pattern = re.compile(
        r"^\s*export\s+(?:default\s+)?(?:async\s+)?"
        r"(class|function|interface|type|const|let|var)\s+([A-Za-z_$][\w$]*)",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        declarations.append(f"{match.group(1)} {match.group(2)}")
    return declarations


def _python_public_declarations(path: Path, text: str) -> list[str]:
    try:
        module = ast.parse(text, filename=path.as_posix())
    except SyntaxError as exc:
        raise ValueError(f"cannot parse selected Python file: {path.name}") from exc
    declarations: list[str] = []
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith(
            "_"
        ):
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            declaration = f"{prefix} {node.name}({ast.unparse(node.args)})"
            if node.returns is not None:
                declaration += f" -> {ast.unparse(node.returns)}"
            declarations.append(declaration)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            bases = ", ".join(ast.unparse(base) for base in node.bases)
            declarations.append(f"class {node.name}" + (f"({bases})" if bases else ""))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            declarations.extend(_public_assignment_names(node))
    return declarations


def _public_assignment_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return sorted(
        target.id
        for target in targets
        if isinstance(target, ast.Name) and not target.id.startswith("_")
    )


def _snapshot_digest(entries: list[dict[str, Any]]) -> str:
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _run_glob(action: dict[str, Any], workspace: Path, policy: RunnerPolicy) -> dict[str, Any]:
    config = _action_config(action)
    pattern = str(config["pattern"])
    if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
        raise ValueError("glob pattern must be workspace-relative")
    matches = []
    for raw in globlib.iglob(str(workspace / pattern), recursive=True):
        path = Path(raw)
        rel = _relative_path(workspace, path)
        if _runtime_excluded(Path(rel), policy):
            continue
        matches.append(rel)
    matches = sorted(set(matches))[: policy.list_items]
    return {"matches": matches, "pattern": pattern}


def _run_visual_diff(
    action: dict[str, Any], workspace: Path, policy: RunnerPolicy
) -> dict[str, Any]:
    config = _action_config(action)
    reference = _visual_path(config, workspace, "reference")
    actual = _visual_path(config, workspace, "actual")
    diff = _visual_path(config, workspace, "diff")
    threshold = float(config["threshold"])
    argv_template = _visual_argv_template(config)
    timeout_sec = float(action["timeout_sec"])
    regions = _visual_regions(config.get("regions", []))

    result = _visual_diff_base_result(workspace, actual, reference, diff, threshold, regions)
    if reference.exists() and actual.exists():
        result["global"] = _run_visual_compare(
            reference,
            actual,
            diff,
            workspace,
            threshold,
            argv_template,
            timeout_sec,
            policy,
        )
        result["diff"] = _path_presence(workspace, diff)
    result["regions"] = [
        _compare_visual_region(
            region,
            workspace,
            threshold,
            argv_template,
            timeout_sec,
            policy,
        )
        for region in _normalized_weight_regions(regions)
    ]
    result["available_region_count"] = sum(
        1 for region in result["regions"] if region.get("comparison") is not None
    )
    return result


def _visual_path(config: dict[str, Any], workspace: Path, key: str) -> Path:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"visual_diff config.{key} must be non-empty text")
    return _resolved_path(workspace, value)


def _visual_argv_template(config: dict[str, Any]) -> list[str]:
    template = config.get("argv_template")
    if (
        not isinstance(template, list)
        or not template
        or not all(isinstance(item, str) for item in template)
    ):
        raise ValueError("visual_diff config.argv_template must be a text list")
    required = {"{reference}", "{actual}", "{diff}", "{threshold}"}
    if not required.issubset(template):
        raise ValueError("visual_diff argv_template is missing required placeholders")
    return template


def _visual_diff_base_result(
    workspace: Path,
    actual: Path,
    reference: Path,
    diff: Path,
    threshold: float,
    regions: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "actual": _path_presence(workspace, actual),
        "reference": _path_presence(workspace, reference),
        "diff": _path_presence(workspace, diff),
        "threshold": threshold,
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
        raise ValueError("visual_diff config.regions must be a list")
    expected = {"name", "reference_image", "actual_image", "diff_image", "weight"}
    for region in value:
        _validate_visual_region(region, expected)
    return value


def _validate_visual_region(value: Any, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("visual_diff regions must define " + ", ".join(sorted(expected)))
    _validate_visual_region_name(value["name"])
    _validate_visual_region_weight(value["weight"])
    _validate_text_fields(
        "visual_diff region",
        value,
        ("reference_image", "actual_image", "diff_image"),
    )


def _validate_visual_region_name(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("visual_diff region name must be non-empty text")


def _validate_visual_region_weight(value: Any) -> None:
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise ValueError("visual_diff region weight must be positive")


def _normalized_weight_regions(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(float(region["weight"]) for region in regions)
    if total <= 0:
        return [{**region, "normalized_weight": 0.0} for region in regions]
    return [{**region, "normalized_weight": float(region["weight"]) / total} for region in regions]


def _compare_visual_region(
    region: dict[str, Any],
    workspace: Path,
    threshold: float,
    argv_template: list[str],
    timeout_sec: float,
    policy: RunnerPolicy,
) -> dict[str, Any]:
    reference = _resolved_path(workspace, str(region["reference_image"]))
    actual = _resolved_path(workspace, str(region["actual_image"]))
    diff = _resolved_path(workspace, str(region["diff_image"]))
    comparison = None
    if reference.exists() and actual.exists():
        comparison = _run_visual_compare(
            reference,
            actual,
            diff,
            workspace,
            threshold,
            argv_template,
            timeout_sec,
            policy,
        )
    return {
        "name": str(region.get("name", "")),
        "weight": float(region.get("weight", 0)),
        "normalized_weight": float(region.get("normalized_weight", 0)),
        "actual": _path_presence(workspace, actual),
        "reference": _path_presence(workspace, reference),
        "diff": _path_presence(workspace, diff),
        "comparison": comparison,
    }


def _run_visual_compare(
    reference: Path,
    actual: Path,
    diff: Path,
    workspace: Path,
    threshold: float,
    argv_template: list[str],
    timeout_sec: float,
    policy: RunnerPolicy,
) -> dict[str, Any]:
    diff.parent.mkdir(parents=True, exist_ok=True)
    replacements = {
        "{reference}": _relative_path(workspace, reference),
        "{actual}": _relative_path(workspace, actual),
        "{diff}": _relative_path(workspace, diff),
        "{threshold}": str(threshold),
    }
    command = [replacements.get(argument, argument) for argument in argv_template]
    completed = subprocess.run(
        command,
        cwd=workspace,
        text=True,
        capture_output=True,
        timeout=timeout_sec,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}".strip()
    similarity = 1.0 if completed.returncode == 0 else _similarity_from_output(output)
    return {
        "command": command,
        "exit_code": completed.returncode,
        "similarity": similarity,
        "raw_output": sanitize_persisted_text(output, max_chars=policy.text_chars),
        "diff_path": _relative_path(workspace, diff) if diff.exists() else None,
    }


def _similarity_from_output(output: str) -> float:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", output)
    if not match:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (float(match.group(1)) / 100.0)))


def _run_archive(
    action: dict[str, Any],
    workspace: Path,
    output_dir: Path,
    policy: RunnerPolicy,
) -> dict[str, Any]:
    config = _action_config(action)
    archive_path = _resolved_output_path(output_dir, str(config["output"]))
    paths = [config["path"]]
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archived: list[str] = []
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in _archive_files(paths, workspace, policy):
            rel = _relative_path(workspace, path)
            archive.add(path, arcname=rel)
            archived.append(rel)
    return {
        "archive": str(archive_path),
        "archived": archived,
        "exists": archive_path.exists(),
    }


def _resolved_output_path(output_dir: Path, value: str) -> Path:
    path = (output_dir / value).resolve()
    try:
        path.relative_to(output_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"output path escapes evidence directory: {value}") from exc
    return path


def _archive_files(paths: list[Any], workspace: Path, policy: RunnerPolicy) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = _resolved_path(workspace, str(raw))
        if not path.exists():
            continue
        if path.is_file() and not _runtime_excluded(Path(_relative_path(workspace, path)), policy):
            files.append(path)
        if path.is_dir():
            files.extend(_archive_dir_files(path, workspace, policy))
    return sorted(files)


def _archive_dir_files(path: Path, workspace: Path, policy: RunnerPolicy) -> list[Path]:
    return [
        child
        for child in path.rglob("*")
        if child.is_file() and not _runtime_excluded(Path(_relative_path(workspace, child)), policy)
    ]


def _execute_action(
    action: dict[str, Any],
    workspace: Path,
    output_dir: Path,
    policy: RunnerPolicy,
) -> dict[str, Any]:
    kind = action["kind"]
    if kind == "command":
        return _run_command(action, workspace, policy)
    if kind == "read_file":
        return _run_read_file(action, workspace, policy)
    if kind == "read_json":
        return _run_read_json(action, workspace, policy)
    if kind == "structural_snapshot":
        return _run_structural_snapshot(action, workspace, policy)
    if kind == "public_contract_snapshot":
        return _run_public_contract_snapshot(action, workspace, policy)
    if kind == "glob":
        return _run_glob(action, workspace, policy)
    if kind == "visual_diff":
        return _run_visual_diff(action, workspace, policy)
    if kind == "archive":
        return _run_archive(action, workspace, output_dir, policy)
    raise ValueError(f"unsupported evidence action kind: {kind}")


def _action_config(action: dict[str, Any]) -> dict[str, Any]:
    config = action["config"]
    if not isinstance(config, dict):
        raise ValueError("verification action config must be an object")
    return config


def run_evidence_plan(
    plan_path: Path,
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Run raw evidence actions from a resolved plan and persist evidence.json."""

    plan = _validated_plan(_read_json(plan_path), plan_path)
    workspace = Path(plan["workspace"]).resolve()
    resolved_output_dir = (
        output_dir.resolve() if output_dir is not None else Path(plan["output_dir"]).resolve()
    )
    actions = plan["actions"]
    policy = _policy_from_plan(plan)
    action_results = _run_actions(actions, workspace, resolved_output_dir, policy)
    evidence = _evidence_output(plan, workspace, action_results)
    _write_json(resolved_output_dir / EVIDENCE_FILENAME, evidence)
    return evidence


def _run_actions(
    actions: list[dict[str, Any]],
    workspace: Path,
    output_dir: Path,
    policy: RunnerPolicy,
) -> list[dict[str, Any]]:
    action_results: list[dict[str, Any]] = []
    status_by_id: dict[str, str] = {}
    logs_dir = output_dir / LOG_DIRNAME

    for index, action in enumerate(actions):
        identifier = action["id"]
        failed_dependencies = [
            dependency
            for dependency in action["depends_on"]
            if status_by_id.get(dependency) != "ok"
        ]
        record = (
            _skipped_action_record(action, failed_dependencies)
            if failed_dependencies
            else _action_record(action, workspace, output_dir, policy)
        )
        action_results.append(record)
        status_by_id[identifier] = record["status"]
        _write_json(logs_dir / f"{index + 1:03d}-{identifier}.json", record)
    return action_results


def _evidence_output(
    plan: dict[str, Any], workspace: Path, action_results: list[dict[str, Any]]
) -> dict[str, Any]:
    artifacts = [
        {
            "action_id": item["id"],
            "path": item["result"]["archive"],
            "exists": item["result"]["exists"],
        }
        for item in action_results
        if item["kind"] == "archive" and item["status"] == "ok"
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "runner_contract": RUNNER_CONTRACT,
        "aliases": plan["aliases"],
        "actions": action_results,
        "artifacts": artifacts,
        "metadata": {
            "action_count": len(action_results),
            "error_count": sum(1 for item in action_results if item["status"] != "ok"),
            "required_error_count": sum(
                1 for item in action_results if item["required"] and item["status"] != "ok"
            ),
            "workspace": str(workspace),
        },
    }


def _action_record(
    action: dict[str, Any],
    workspace: Path,
    output_dir: Path,
    policy: RunnerPolicy,
) -> dict[str, Any]:
    started = time.monotonic()
    record: dict[str, Any] = {
        "id": action["id"],
        "kind": action["kind"],
        "required": action["required"],
        "status": "ok",
    }
    try:
        record["result"] = _execute_action(action, workspace, output_dir, policy)
    except Exception as exc:  # noqa: BLE001 - action errors are evidence
        record["status"] = "error"
        record["error"] = _action_error(exc)
    record["duration_sec"] = round(time.monotonic() - started, 3)
    return record


def _skipped_action_record(
    action: dict[str, Any], failed_dependencies: list[str]
) -> dict[str, Any]:
    return {
        "id": action["id"],
        "kind": action["kind"],
        "required": action["required"],
        "status": "skipped",
        "duration_sec": 0.0,
        "error": {
            "message": f"dependencies did not succeed: {', '.join(failed_dependencies)}",
            "type": "DependencyFailed",
        },
    }


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
