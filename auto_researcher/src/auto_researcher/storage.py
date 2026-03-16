"""Filesystem layout and IO helpers for autoresearch objectives."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .models import ObjectiveState

VERSION_PATTERN = re.compile(r"^v(\d+)$")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "objective"


@dataclass(frozen=True, slots=True)
class WorkspaceLayout:
    """Repository-relative paths for autoresearch and evaluator artifacts."""

    repo_root: Path

    @property
    def auto_researcher_root(self) -> Path:
        return self.repo_root / "auto_researcher"

    @property
    def objectives_root(self) -> Path:
        return self.auto_researcher_root / "objectives"

    @property
    def roles_root(self) -> Path:
        return self.auto_researcher_root / "roles"

    @property
    def scenarios_root(self) -> Path:
        return self.repo_root / "scenarios"

    @property
    def benchmark_experiments_root(self) -> Path:
        return self.repo_root / "experiments" / "benchmarks"

    @property
    def research_loop_experiments_root(self) -> Path:
        return self.repo_root / "experiments" / "research_loops"

    def objective_root(self, objective_id: str) -> Path:
        return self.objectives_root / objective_id

    def objective_state_path(self, objective_id: str) -> Path:
        return self.objective_root(objective_id) / "objective.yaml"

    def objective_brief_path(self, objective_id: str) -> Path:
        return self.objective_root(objective_id) / "brief.md"

    def objective_report_path(self, objective_id: str) -> Path:
        return self.objective_root(objective_id) / "report.md"

    def objective_plan_dir(self, objective_id: str) -> Path:
        return self.objective_root(objective_id) / "plans"

    def objective_review_dir(self, objective_id: str) -> Path:
        return self.objective_root(objective_id) / "reviews"

    def objective_draft_root(self, objective_id: str, scenario_slug: str) -> Path:
        return self.objective_root(objective_id) / "drafts" / "scenario" / scenario_slug

    def objective_loops_root(self, objective_id: str) -> Path:
        return self.objective_root(objective_id) / "loops"

    def loop_root(self, objective_id: str, loop_id: str) -> Path:
        return self.objective_loops_root(objective_id) / loop_id

    def loop_state_path(self, objective_id: str, loop_id: str) -> Path:
        return self.loop_root(objective_id, loop_id) / "state.json"

    def loop_snapshots_dir(self, objective_id: str, loop_id: str) -> Path:
        return self.loop_root(objective_id, loop_id) / "snapshots"

    def loop_diffs_dir(self, objective_id: str, loop_id: str) -> Path:
        return self.loop_root(objective_id, loop_id) / "diffs"

    def loop_candidate_root(self, objective_id: str, loop_id: str, scenario_slug: str) -> Path:
        return self.loop_root(objective_id, loop_id) / "candidate" / scenario_slug

    def role_root(self, objective_id: str, role: str) -> Path:
        return self.objective_root(objective_id) / "roles" / role

    def role_sessions_dir(self, objective_id: str, role: str) -> Path:
        return self.role_root(objective_id, role) / "sessions"

    def role_requests_dir(self, objective_id: str, role: str) -> Path:
        return self.role_root(objective_id, role) / "requests"

    def role_responses_dir(self, objective_id: str, role: str) -> Path:
        return self.role_root(objective_id, role) / "responses"

    def role_events_dir(self, objective_id: str, role: str) -> Path:
        return self.role_root(objective_id, role) / "events"

    def role_prompt_path(self, role: str) -> Path:
        return self.roles_root / f"{role}.md"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_text(path: Path, content: str) -> None:
    ensure_dir(path.parent)
    path.write_text(content, encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, indent=2) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(read_text(path))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def write_yaml(path: Path, payload: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(read_text(path))
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root must be an object: {path}")
    return payload


def write_objective_state(path: Path, objective: ObjectiveState) -> None:
    write_yaml(path, objective.model_dump(mode="json", exclude_none=True))


def load_objective_state(path: Path) -> ObjectiveState:
    return ObjectiveState.model_validate(read_yaml(path))


def copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination)


def overlay_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, dirs_exist_ok=True)


def sync_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        for path in sorted(destination.rglob("*"), reverse=True):
            relpath = path.relative_to(destination)
            if (source / relpath).exists():
                continue
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
    shutil.copytree(source, destination, dirs_exist_ok=True)


def snapshot_tree(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    if not root.exists():
        return snapshot
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relpath = path.relative_to(root).as_posix()
        snapshot[relpath] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _read_text_if_utf8(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def write_compact_tree_diff(
    before_root: Path, after_root: Path, output_path: Path
) -> dict[str, Any]:
    before = snapshot_tree(before_root)
    after = snapshot_tree(after_root)
    changed_paths = sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )
    changed_files: list[dict[str, Any]] = []
    for relpath in changed_paths:
        before_path = before_root / relpath
        after_path = after_root / relpath
        if relpath not in before:
            change_type = "added"
        elif relpath not in after:
            change_type = "deleted"
        else:
            change_type = "modified"

        entry: dict[str, Any] = {
            "path": relpath,
            "change_type": change_type,
            "before_sha256": before.get(relpath),
            "after_sha256": after.get(relpath),
            "text_diff_available": False,
        }
        before_text = _read_text_if_utf8(before_path) if before_path.is_file() else ""
        after_text = _read_text_if_utf8(after_path) if after_path.is_file() else ""
        if before_text is not None and after_text is not None:
            diff_lines = list(
                difflib.unified_diff(
                    before_text.splitlines(),
                    after_text.splitlines(),
                    fromfile=f"a/{relpath}",
                    tofile=f"b/{relpath}",
                    lineterm="",
                    n=3,
                )
            )
            entry["text_diff_available"] = True
            entry["diff_excerpt"] = "\n".join(diff_lines[:40])
            entry["diff_truncated"] = len(diff_lines) > 40
        changed_files.append(entry)

    payload = {
        "before_root": str(before_root),
        "after_root": str(after_root),
        "changed_files": changed_files,
    }
    write_json(output_path, payload)
    return payload


def illegal_mutations(
    before: dict[str, str], after: dict[str, str], allowed_prefixes: list[str]
) -> list[str]:
    changed = {path for path in set(before) | set(after) if before.get(path) != after.get(path)}
    return [
        path
        for path in sorted(changed)
        if not any(path == prefix or path.startswith(prefix) for prefix in allowed_prefixes)
    ]


def update_scenario_document(
    scenario_yaml: Path,
    *,
    name: str,
    description: str,
    difficulty: str,
    category: str,
    timeout_sec: int,
    starter_root: str,
    prompt_entry: str,
    metric_ids: list[str],
    required_commands: list[list[str]],
    gates: list[dict[str, Any]],
) -> None:
    document = read_yaml(scenario_yaml)
    document["name"] = name
    document["description"] = description
    document["difficulty"] = difficulty
    document["category"] = category
    document["timeout_sec"] = timeout_sec
    document["starter"] = {"root": starter_root}
    document["prompt"] = {"entry": prompt_entry, "includes": []}
    document["metrics"] = [{"type": "core", "id": metric_id} for metric_id in metric_ids]
    document["verification"] = {
        **dict(document.get("verification") or {}),
        "required_commands": required_commands,
        "gates": gates,
    }
    write_yaml(scenario_yaml, document)


def update_scenario_revision(scenario_yaml: Path, revision: str) -> None:
    document = read_yaml(scenario_yaml)
    document["scenario_revision"] = revision
    write_yaml(scenario_yaml, document)


def scenario_timeout_sec(scenario_yaml: Path) -> int:
    document = read_yaml(scenario_yaml)
    timeout = document.get("timeout_sec")
    if not isinstance(timeout, int):
        raise ValueError(f"Scenario timeout_sec must be an integer: {scenario_yaml}")
    return timeout


def scenario_root_from_yaml(scenario_yaml: Path) -> Path:
    return scenario_yaml.parent.parent


def latest_revision_name(scenario_root: Path) -> str:
    revisions = [path.name for path in scenario_root.iterdir() if path.is_dir()]
    ranked = []
    for revision in revisions:
        match = VERSION_PATTERN.fullmatch(revision)
        if match is None:
            continue
        ranked.append((int(match.group(1)), revision))
    if not ranked:
        raise FileNotFoundError(f"No scenario revisions found in {scenario_root}")
    return max(ranked)[1]


def experiment_summary(path: Path) -> dict[str, Any]:
    summary_path = path
    if path.is_dir():
        summary_path = path / "experiment-summary.json"
    return read_json(summary_path)


def append_line(path: Path, line: str) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
