"""Deterministic scripted runners for demo and smoke workflows."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import ObjectiveInitRequest, RoleModelConfig
from .pi_rpc import RoleExecution, RoleRunner
from .raidar_cli import RaidarClient
from .storage import WorkspaceLayout, ensure_dir, read_yaml, utc_now_iso, write_text


def load_objective_fixture(path: Path) -> ObjectiveInitRequest:
    payload = read_yaml(path)
    return ObjectiveInitRequest.model_validate(payload)


def load_script_fixture(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Script fixture must be a JSON object: {path}")
    roles = payload.get("roles")
    experiments = payload.get("experiments")
    if not isinstance(roles, list) or not isinstance(experiments, list):
        raise ValueError("Script fixture must define list-valued 'roles' and 'experiments'.")
    return list(roles), list(experiments)


@dataclass(slots=True)
class ScriptedRoleRunner(RoleRunner):
    """Replay a deterministic sequence of role calls from a fixture."""

    layout: WorkspaceLayout
    scripts: list[dict[str, Any]]

    def validate(self) -> None:
        return None

    def run_role(
        self,
        *,
        objective_id: str,
        role: str,
        instruction: str,
        model: RoleModelConfig,
    ) -> RoleExecution:
        del model
        if not self.scripts:
            raise RuntimeError(f"Unexpected role invocation: {role}")
        script = self.scripts.pop(0)
        if script.get("role") != role:
            raise RuntimeError(f"Expected scripted role {script.get('role')}, got {role}")

        timestamp = utc_now_iso().replace(":", "").replace("+00:00", "Z")
        session_dir = ensure_dir(self.layout.role_sessions_dir(objective_id, role))
        request_path = self.layout.role_requests_dir(objective_id, role) / f"{timestamp}.md"
        response_path = self.layout.role_responses_dir(objective_id, role) / f"{timestamp}.md"
        events_path = self.layout.role_events_dir(objective_id, role) / f"{timestamp}.jsonl"
        write_text(request_path, instruction.rstrip() + "\n")

        self._apply_candidate_edits(script, instruction)
        output_path = _extract_output_path(instruction)
        payload = script.get("payload")
        if output_path is not None and payload is not None:
            write_text(Path(output_path), json.dumps(payload, indent=2) + "\n")

        assistant_text = str(script.get("assistant_text") or role)
        write_text(response_path, assistant_text + "\n")
        write_text(
            events_path,
            json.dumps({"role": role, "assistant_text": assistant_text}, indent=2) + "\n",
        )
        return RoleExecution(
            role=role,
            session_dir=session_dir,
            request_path=request_path,
            response_path=response_path,
            events_path=events_path,
            assistant_text=assistant_text,
        )

    def _apply_candidate_edits(self, script: dict[str, Any], instruction: str) -> None:
        candidate_yaml = _extract_path("Candidate scenario yaml:", instruction)
        if candidate_yaml is None:
            return
        writes = script.get("write_files")
        if not isinstance(writes, list):
            return
        candidate_root = Path(candidate_yaml).parent
        for item in writes:
            if not isinstance(item, dict):
                raise RuntimeError("write_files entries must be objects.")
            relpath = item.get("path")
            content = item.get("content")
            if not isinstance(relpath, str) or not isinstance(content, str):
                raise RuntimeError("write_files entries require string path and content.")
            write_text(candidate_root / relpath, content)


@dataclass(slots=True)
class ScriptedRaidar(RaidarClient):
    """Deterministic evaluator double for smoke workflows."""

    layout: WorkspaceLayout
    experiment_payloads: list[dict[str, Any]]

    def scenario_init(
        self,
        *,
        path: Path,
        name: str,
        scenario_revision: str,
        starter_root: str,
        prompt_entry: str,
        difficulty: str,
        category: str,
        timeout_sec: int,
    ) -> dict[str, Any]:
        revision_dir = path / scenario_revision
        ensure_dir(revision_dir / "rules")
        ensure_dir(revision_dir / "prompt")
        scenario_yaml = revision_dir / "scenario.yaml"
        scenario_yaml.write_text(
            yaml.safe_dump(
                {
                    "name": name,
                    "scenario_revision": scenario_revision,
                    "description": f"Scenario definition for {name}",
                    "difficulty": difficulty,
                    "category": category,
                    "timeout_sec": timeout_sec,
                    "dockerfile": "./Dockerfile",
                    "test_scripts": [],
                    "starter": {"root": starter_root},
                    "verification": {
                        "max_gate_failures": 3,
                        "min_quality_score": 0.8,
                        "required_commands": [],
                        "gates": [],
                    },
                    "acceptance": {
                        "deterministic_checks": [],
                        "requirements": [],
                        "llm_judge_rubric": [],
                    },
                    "metrics": [{"type": "core", "id": "functional"}],
                    "prompt": {"entry": prompt_entry, "includes": []},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        prompt_path = revision_dir / prompt_entry
        write_text(prompt_path, "Initial prompt\n")
        ensure_dir(revision_dir / starter_root)
        return {
            "scenario_root": str(path),
            "scenario_name": name,
            "scenario_revision": scenario_revision,
            "revision_dir": str(revision_dir),
            "scenario_yaml": str(scenario_yaml),
            "prompt_path": str(prompt_path),
            "rules_dir": str(revision_dir / "rules"),
            "starter_root": starter_root,
        }

    def scenario_validate(self, *, scenario_yaml: Path) -> None:
        if not scenario_yaml.is_file():
            raise RuntimeError(f"Missing scenario yaml: {scenario_yaml}")

    def scenario_clone_revision(
        self,
        *,
        path: Path,
        from_revision: str,
        to_revision: str | None = None,
    ) -> dict[str, Any]:
        numeric = int(from_revision.removeprefix("v")) + 1
        target_revision = to_revision or f"v{numeric:03d}"
        source_dir = path / from_revision
        target_dir = path / target_revision
        shutil.copytree(source_dir, target_dir)
        scenario_yaml = target_dir / "scenario.yaml"
        document = read_yaml(scenario_yaml)
        document["scenario_revision"] = target_revision
        scenario_yaml.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        return {
            "scenario_root": str(path),
            "source_revision": from_revision,
            "target_revision": target_revision,
            "revision_dir": str(target_dir),
            "scenario_yaml": str(scenario_yaml),
        }

    def experiment_run(
        self,
        *,
        scenario_yaml: Path,
        harness: str,
        model: str,
        timeout_sec: int,
        repeats: int,
        repeat_parallel: int,
        experiment_kind: str,
        experiments_root: Path | None = None,
    ) -> dict[str, Any]:
        del timeout_sec, repeats, repeat_parallel
        if not self.experiment_payloads:
            raise RuntimeError("Scripted evaluator ran out of experiment payloads.")
        root = experiments_root
        if root is None:
            root = (
                self.layout.benchmark_experiments_root
                if experiment_kind == "benchmark"
                else self.layout.research_loop_experiments_root
            )
        execution_dir = root / f"exp-{len(list(root.glob('exp-*'))) + 1:02d}"
        ensure_dir(execution_dir)
        summary_path = execution_dir / "experiment-summary.json"
        summary_path.write_text(
            json.dumps(self.experiment_payloads.pop(0), indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "scenario_path": str(scenario_yaml),
            "scenario_name": scenario_yaml.parent.parent.name,
            "scenario_revision": scenario_yaml.parent.name,
            "summary_path": str(summary_path),
            "report_path": str(execution_dir / "report.md"),
            "experiment_json_path": str(execution_dir / "experiment.json"),
            "runs": [],
            "retries_used": 0,
            "harness": harness,
            "model": model,
        }


def _extract_output_path(instruction: str) -> str | None:
    for line in instruction.splitlines():
        match = re.search(r"Write .* to: (.+)$", line)
        if match is not None:
            return match.group(1).strip()
    return None


def _extract_path(prefix: str, instruction: str) -> str | None:
    for line in instruction.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return None
