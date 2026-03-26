"""Workflow tests for PI-driven autoresearch orchestration."""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from raidar.application.models import (
    ExperimentRunRequest,
    ScenarioCloneRequest,
    ScenarioInitRequest,
    ScenarioInitResult,
    ScenarioValidationResult,
    SuiteExecutionResult,
)
from raidar.scenario_clone import ScenarioCloneResult
from raidar.schemas.scenario import ScenarioDefinition

from auto_researcher.engine import AutoResearchEngine
from auto_researcher.models import ObjectiveInitRequest, ObjectiveState
from auto_researcher.pi_rpc import RoleExecution
from auto_researcher.storage import WorkspaceLayout, read_json, read_yaml


def _summary_payload(
    *,
    scores: tuple[float, float, float],
    metric_ids: list[str],
    unscored_count: int = 0,
    validity_rate: float = 1.0,
    performance_pass_rate: float = 1.0,
) -> dict[str, Any]:
    composite, quality, diagnostic = scores
    return {
        "aggregate": {
            "unscored_count": unscored_count,
            "validity_rate": validity_rate,
            "performance_pass_rate": performance_pass_rate,
            "metric_outcomes": {
                metric_id: {"pass_rate": 1.0, "pass_count": 1, "fail_count": 0, "sample_size": 1}
                for metric_id in metric_ids
            },
            "composite_score": {"mean": composite},
            "quality_score": {"mean": quality},
            "diagnostic_score": {"mean": diagnostic},
        }
    }


class FakeRoleRunner:
    def __init__(self, layout: WorkspaceLayout, scripts: list[dict[str, Any]]) -> None:
        self.layout = layout
        self.scripts = list(scripts)
        self.calls: list[dict[str, Any]] = []

    def validate(self) -> None:
        return None

    def run_role(
        self,
        *,
        objective_id: str,
        role: str,
        instruction: str,
        model: Any,
    ) -> RoleExecution:
        if not self.scripts:
            raise AssertionError(f"Unexpected role invocation: {role}")
        script = self.scripts.pop(0)
        assert script["role"] == role
        session_dir = self.layout.role_sessions_dir(objective_id, role)
        session_dir.mkdir(parents=True, exist_ok=True)
        request_path = self.layout.role_requests_dir(objective_id, role) / "request.md"
        response_path = self.layout.role_responses_dir(objective_id, role) / "response.md"
        events_path = self.layout.role_events_dir(objective_id, role) / "events.jsonl"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(instruction, encoding="utf-8")
        if script.get("edit") is not None:
            candidate_yaml = _extract_path("Candidate scenario yaml:", instruction)
            assert candidate_yaml is not None
            script["edit"](Path(candidate_yaml))
        output_path = _extract_output_path(instruction)
        if output_path is not None and script.get("payload") is not None:
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(script["payload"], indent=2) + "\n", encoding="utf-8")
        response_path.write_text(script.get("assistant_text", role), encoding="utf-8")
        events_path.write_text("{}", encoding="utf-8")
        self.calls.append(
            {
                "role": role,
                "session_dir": session_dir,
                "instruction": instruction,
                "model": model,
            }
        )
        return RoleExecution(
            role=role,
            session_dir=session_dir,
            request_path=request_path,
            response_path=response_path,
            events_path=events_path,
            assistant_text=script.get("assistant_text", role),
        )


class DynamicRoleRunner:
    def __init__(
        self,
        layout: WorkspaceLayout,
        payload_factory: Callable[[str, str], dict[str, Any]],
    ) -> None:
        self.layout = layout
        self.payload_factory = payload_factory
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._call_index = 0

    def validate(self) -> None:
        return None

    def run_role(
        self,
        *,
        objective_id: str,
        role: str,
        instruction: str,
        model: Any,
    ) -> RoleExecution:
        with self._lock:
            self._call_index += 1
            call_index = self._call_index

        payload = self.payload_factory(role, instruction)
        session_dir = self.layout.role_sessions_dir(objective_id, role)
        session_dir.mkdir(parents=True, exist_ok=True)
        request_path = self.layout.role_requests_dir(objective_id, role) / f"{call_index:03d}.md"
        response_path = self.layout.role_responses_dir(objective_id, role) / f"{call_index:03d}.md"
        events_path = self.layout.role_events_dir(objective_id, role) / f"{call_index:03d}.jsonl"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(instruction, encoding="utf-8")
        if payload.get("edit") is not None:
            candidate_yaml = _extract_path("Candidate scenario yaml:", instruction)
            assert candidate_yaml is not None
            payload["edit"](Path(candidate_yaml))
        output_path = _extract_output_path(instruction)
        if output_path is not None and payload.get("payload") is not None:
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(payload["payload"], indent=2) + "\n",
                encoding="utf-8",
            )
        assistant_text = str(payload.get("assistant_text", role))
        response_path.write_text(assistant_text, encoding="utf-8")
        events_path.write_text("{}", encoding="utf-8")
        with self._lock:
            self.calls.append(
                {
                    "role": role,
                    "session_dir": session_dir,
                    "instruction": instruction,
                    "model": model,
                }
            )
        return RoleExecution(
            role=role,
            session_dir=session_dir,
            request_path=request_path,
            response_path=response_path,
            events_path=events_path,
            assistant_text=assistant_text,
        )


class FakeRaidar:
    def __init__(
        self,
        layout: WorkspaceLayout,
        experiment_payloads: list[dict[str, Any]] | None = None,
    ) -> None:
        self.layout = layout
        self.experiment_payloads = list(experiment_payloads or [])
        self.experiment_calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def scenario_init(self, request: ScenarioInitRequest) -> ScenarioInitResult:
        revision_dir = request.path / request.scenario_revision
        (revision_dir / "rules").mkdir(parents=True, exist_ok=True)
        (revision_dir / "prompt").mkdir(parents=True, exist_ok=True)
        scenario_yaml = revision_dir / "scenario.yaml"
        scenario_yaml.write_text(
            yaml.safe_dump(
                {
                    "name": request.name or request.path.name,
                    "scenario_revision": request.scenario_revision,
                    "description": f"Scenario definition for {request.name or request.path.name}",
                    "difficulty": request.difficulty,
                    "category": request.category,
                    "timeout_sec": request.timeout_sec,
                    "dockerfile": "./Dockerfile",
                    "test_scripts": [],
                    "starter": {"root": request.starter_root},
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
                    "prompt": {"entry": request.prompt_entry, "includes": []},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        prompt_path = revision_dir / request.prompt_entry
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("Initial prompt\n", encoding="utf-8")
        return ScenarioInitResult(
            scenario_root=request.path,
            scenario_name=request.name or request.path.name,
            scenario_revision=request.scenario_revision,
            revision_dir=revision_dir,
            scenario_yaml=scenario_yaml,
            prompt_path=prompt_path,
            rules_dir=revision_dir / "rules",
            starter_root=request.starter_root,
        )

    def scenario_validate(self, *, scenario_yaml: Path) -> ScenarioValidationResult:
        assert scenario_yaml.is_file()
        return ScenarioValidationResult(
            scenario_path=scenario_yaml,
            scenario=ScenarioDefinition.model_validate(read_yaml(scenario_yaml)),
        )

    def scenario_clone_revision(self, request: ScenarioCloneRequest) -> ScenarioCloneResult:
        numeric = int(request.from_revision.removeprefix("v")) + 1
        target_revision = request.to_revision or f"v{numeric:03d}"
        source_dir = request.path / request.from_revision
        target_dir = request.path / target_revision
        shutil.copytree(source_dir, target_dir)
        scenario_yaml = target_dir / "scenario.yaml"
        document = read_yaml(scenario_yaml)
        document["scenario_revision"] = target_revision
        scenario_yaml.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        return ScenarioCloneResult(
            scenario_root=request.path,
            source_revision=request.from_revision,
            target_revision=target_revision,
            target_scenario_yaml=scenario_yaml,
        )

    def experiment_run(self, request: ExperimentRunRequest) -> SuiteExecutionResult:
        with self._lock:
            call_index = len(self.experiment_calls) + 1
            payload = self.experiment_payloads.pop(0)
            if request.experiment_kind == "benchmark":
                root = self.layout.benchmark_experiments_root
            else:
                root = self.layout.research_loop_experiments_root
        execution_dir = root / f"exp-{call_index:02d}"
        execution_dir.mkdir(parents=True, exist_ok=True)
        (execution_dir / "experiment-summary.json").write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        with self._lock:
            self.experiment_calls.append(
                {
                    "experiment_kind": request.experiment_kind,
                    "scenario_yaml": request.scenario,
                    "harness": request.harness,
                    "model": request.model,
                    "repeats": request.repeats,
                    "repeat_parallel": request.repeat_parallel,
                    "summary_path": execution_dir / "experiment-summary.json",
                }
            )
        return SuiteExecutionResult(
            scenario_path=request.scenario,
            scenario_name=request.scenario.parent.parent.name,
            scenario_revision=request.scenario.parent.name,
            runs=[],
            retries_used=0,
            experiment_json_path=execution_dir / "experiment.json",
            summary_path=execution_dir / "experiment-summary.json",
            report_path=execution_dir / "report.md",
        )


def _extract_output_path(instruction: str) -> str | None:
    for line in instruction.splitlines():
        match = re.search(r"Write .* to: (.+)$", line)
        if match:
            return match.group(1).strip()
    return None


def _extract_path(prefix: str, instruction: str) -> str | None:
    for line in instruction.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return None


def _make_engine(
    tmp_path: Path,
    *,
    role_scripts: list[dict[str, Any]],
    experiment_payloads: list[dict[str, Any]] | None = None,
) -> tuple[AutoResearchEngine, FakeRoleRunner, FakeRaidar, WorkspaceLayout]:
    layout = WorkspaceLayout(tmp_path)
    layout.auto_researcher_root.mkdir(parents=True, exist_ok=True)
    layout.objectives_root.mkdir(parents=True, exist_ok=True)
    layout.scenarios_root.mkdir(parents=True, exist_ok=True)
    layout.benchmark_experiments_root.mkdir(parents=True, exist_ok=True)
    layout.research_loop_experiments_root.mkdir(parents=True, exist_ok=True)
    role_runner = FakeRoleRunner(layout, role_scripts)
    raidar = FakeRaidar(layout, experiment_payloads=experiment_payloads)
    return (
        AutoResearchEngine(layout=layout, role_runner=role_runner, raidar=raidar),
        role_runner,
        raidar,
        layout,
    )


def _init_request(**overrides: Any) -> ObjectiveInitRequest:
    payload = {
        "goal": "Improve homepage implementation quality",
        "target_harness": "codex-cli",
        "target_model": "codex/gpt-5.4-high",
    }
    payload.update(overrides)
    return ObjectiveInitRequest.model_validate(payload)


def _design_payload() -> dict[str, Any]:
    return {
        "scenario_slug": "homepage-objective",
        "scenario_name": "Homepage Objective",
        "description": "Improve the homepage implementation.",
        "difficulty": "medium",
        "category": "ui",
        "timeout_sec": 600,
        "starter_root": "starter",
        "prompt_entry": "prompt/task.md",
        "prompt_text": "Improve the homepage implementation while preserving validation.",
        "metric_ids": ["functional", "acceptance", "verification-stability"],
        "required_commands": [["bun", "run", "lint"]],
        "gates": [{"name": "lint", "command": ["bun", "run", "lint"]}],
        "starter_files": [
            {
                "path": "package.json",
                "content": json.dumps(
                    {
                        "name": "homepage-objective",
                        "private": True,
                        "type": "module",
                        "devDependencies": {"is-number": "^7.0.0"},
                        "scripts": {
                            "lint": "echo lint",
                            "test": "bun test",
                        },
                    }
                ),
            }
        ],
        "notes": ["draft the first scenario"],
    }


def _critic_payload() -> dict[str, Any]:
    return {"decision": "approve", "summary": "Scenario is measurable.", "risks": []}


def _resolved_loop_id(planner_round: int, raw_loop_id: str) -> str:
    return f"round-{planner_round:02d}__{raw_loop_id}"


def test_init_creates_objective_artifacts_and_draft_only(tmp_path: Path) -> None:
    engine, role_runner, _raidar, layout = _make_engine(
        tmp_path,
        role_scripts=[
            {"role": "designer", "payload": _design_payload()},
            {"role": "critic", "payload": _critic_payload()},
        ],
    )

    objective = engine.init_objective(_init_request())

    assert objective.status == "awaiting_scenario_approval"
    assert objective.loop_execution_mode == "serial"
    draft_yaml = Path(objective.draft_scenario_ref or "")
    assert draft_yaml.is_file()
    assert str(draft_yaml).startswith(str(layout.objectives_root))
    assert (
        (draft_yaml.parent / "starter" / "package.json")
        .read_text(encoding="utf-8")
        .startswith('{"name": "homepage-objective"')
    )
    assert (draft_yaml.parent / "starter" / "bun.lock").is_file()
    assert not any(layout.scenarios_root.rglob("scenario.yaml"))
    assert layout.objective_brief_path(objective.objective_id).is_file()
    assert layout.objective_state_path(objective.objective_id).is_file()
    draft_document = read_yaml(draft_yaml)
    assert draft_document["acceptance"]["deterministic_checks"]
    assert draft_document["acceptance"]["llm_judge_rubric"]
    assert len(role_runner.calls) == 2


def test_init_derives_smoke_acceptance_contract_from_prompt(tmp_path: Path) -> None:
    design = {
        **_design_payload(),
        "scenario_slug": "hello-world-smoke",
        "scenario_name": "Hello World Smoke",
        "prompt_text": (
            "# Task\n\n"
            "Implement a smoke-safe hello world.\n\n"
            "## Requirements\n"
            "1. Export `formatGreeting` from `src/main.ts`.\n\n"
            "## Acceptance criteria\n"
            "- `bun run start` outputs exactly `Hello, Raidar!`\n"
            "- No additional runtime dependencies are introduced\n"
        ),
        "required_commands": [["bun", "run", "lint"], ["bun", "run", "test"]],
        "gates": [{"name": "lint", "command": ["bun", "run", "lint"]}],
        "starter_files": [
            {
                "path": "package.json",
                "content": json.dumps(
                    {
                        "name": "hello-world-smoke",
                        "private": True,
                        "type": "module",
                        "devDependencies": {"typescript": "^5.8.3"},
                        "scripts": {
                            "lint": "echo lint",
                            "test": "bun test",
                            "start": "bun src/main.ts",
                        },
                    }
                ),
            },
            {
                "path": "src/main.ts",
                "content": (
                    "export function formatGreeting(name: string): string {\n"
                    "  return `Hello, ${name}!`;\n"
                    "}\n"
                    "if (import.meta.main) {\n"
                    "  console.log(formatGreeting('Raidar'));\n"
                    "}\n"
                ),
            },
            {
                "path": "test/main.test.ts",
                "content": (
                    'import { expect, test } from "bun:test";\n'
                    'import { formatGreeting } from "../src/main";\n'
                    'test("formats greeting", () => {\n'
                    '  expect(formatGreeting("Raidar")).toBe("Hello, Raidar!");\n'
                    "});\n"
                ),
            },
        ],
    }
    engine, _role_runner, _raidar, _layout = _make_engine(
        tmp_path,
        role_scripts=[
            {"role": "designer", "payload": design},
            {"role": "critic", "payload": _critic_payload()},
        ],
    )

    objective = engine.init_objective(_init_request(goal="Draft hello-world smoke scenario"))

    draft_yaml = Path(objective.draft_scenario_ref or "")
    document = read_yaml(draft_yaml)
    assert ["bun", "run", "start"] in document["verification"]["required_commands"]
    assert any(
        gate["command"] == ["bun", "run", "start"] for gate in document["verification"]["gates"]
    )
    assert any(
        requirement["id"] == "req-start-output"
        for requirement in document["acceptance"]["requirements"]
    )
    assert any(
        "Hello, Raidar!" in criterion["criterion"]
        for criterion in document["acceptance"]["llm_judge_rubric"]
    )


def test_approve_promotes_exact_draft_and_seeds_benchmark(tmp_path: Path) -> None:
    metric_ids = ["functional", "acceptance", "verification-stability"]
    engine, _role_runner, raidar, layout = _make_engine(
        tmp_path,
        role_scripts=[
            {"role": "designer", "payload": _design_payload()},
            {"role": "critic", "payload": _critic_payload()},
        ],
        experiment_payloads=[_summary_payload(scores=(0.75, 0.8, 0.78), metric_ids=metric_ids)],
    )
    created = engine.init_objective(_init_request(max_revisions=1))

    approved = engine.approve_scenario(created.objective_id)

    assert approved.status == "active"
    scenario_ref = Path(approved.scenario_ref or "")
    assert scenario_ref.is_file()
    assert str(scenario_ref).startswith(str(layout.scenarios_root))
    assert read_yaml(Path(approved.draft_scenario_ref or "")) == read_yaml(scenario_ref)
    assert str(approved.best_benchmark_ref).startswith(str(layout.benchmark_experiments_root))
    assert raidar.experiment_calls[0]["experiment_kind"] == "benchmark"


def test_run_refuses_before_scenario_approval(tmp_path: Path) -> None:
    engine, _role_runner, _raidar, _layout = _make_engine(
        tmp_path,
        role_scripts=[
            {"role": "designer", "payload": _design_payload()},
            {"role": "critic", "payload": _critic_payload()},
        ],
    )
    created = engine.init_objective(_init_request(max_revisions=1))

    with pytest.raises(RuntimeError, match="must be active"):
        engine.run_objective(created.objective_id)


def test_roles_use_isolated_session_paths(tmp_path: Path) -> None:
    metric_ids = ["functional", "acceptance", "verification-stability"]
    engine, role_runner, _raidar, _layout = _make_engine(
        tmp_path,
        role_scripts=[
            {"role": "designer", "payload": _design_payload()},
            {"role": "critic", "payload": _critic_payload()},
            {"role": "planner", "payload": {"loops": [], "notes": ["stop"]}},
        ],
        experiment_payloads=[_summary_payload(scores=(0.75, 0.8, 0.78), metric_ids=metric_ids)],
    )

    created = engine.init_objective(_init_request(max_revisions=1))
    engine.approve_scenario(created.objective_id)
    engine.run_objective(created.objective_id)

    session_dirs = {call["role"]: call["session_dir"] for call in role_runner.calls}
    assert session_dirs["designer"] != session_dirs["critic"]
    assert session_dirs["critic"] != session_dirs["planner"]


def test_run_enforces_max_parallel_loop_cap(tmp_path: Path) -> None:
    metric_ids = ["functional", "acceptance", "verification-stability"]
    engine, _role_runner, _raidar, _layout = _make_engine(
        tmp_path,
        role_scripts=[
            {"role": "designer", "payload": _design_payload()},
            {"role": "critic", "payload": _critic_payload()},
            {
                "role": "planner",
                "payload": {
                    "loops": [
                        {
                            "loop_id": "loop-001",
                            "title": "One",
                            "hypothesis": "A",
                            "instructions": "A",
                        },
                        {
                            "loop_id": "loop-002",
                            "title": "Two",
                            "hypothesis": "B",
                            "instructions": "B",
                        },
                        {
                            "loop_id": "loop-003",
                            "title": "Three",
                            "hypothesis": "C",
                            "instructions": "C",
                        },
                        {
                            "loop_id": "loop-004",
                            "title": "Four",
                            "hypothesis": "D",
                            "instructions": "D",
                        },
                    ],
                    "notes": [],
                },
            },
        ],
        experiment_payloads=[_summary_payload(scores=(0.75, 0.8, 0.78), metric_ids=metric_ids)],
    )
    created = engine.init_objective(_init_request(max_revisions=1))
    engine.approve_scenario(created.objective_id)

    with pytest.raises(RuntimeError, match="max_parallel_loops"):
        engine.run_objective(created.objective_id)


def test_promotion_uses_research_and_benchmark_roots_and_updates_best_benchmark(
    tmp_path: Path,
) -> None:
    metric_ids = ["functional", "acceptance", "verification-stability"]

    def _allowed_edit(candidate_yaml: Path) -> None:
        (candidate_yaml.parent / "prompt" / "task.md").write_text(
            "Improved prompt\n", encoding="utf-8"
        )

    engine, role_runner, raidar, layout = _make_engine(
        tmp_path,
        role_scripts=[
            {"role": "designer", "payload": _design_payload()},
            {"role": "critic", "payload": _critic_payload()},
            {
                "role": "planner",
                "payload": {
                    "loops": [
                        {
                            "loop_id": "loop-001",
                            "title": "Prompt refinement",
                            "hypothesis": "Clearer prompt improves results",
                            "instructions": "Tighten prompt wording",
                        }
                    ],
                    "notes": [],
                },
            },
            {
                "role": "executor",
                "payload": {
                    "summary": "Refined the prompt",
                    "changed_files": ["prompt/task.md"],
                    "rationale": "Sharper instruction improves outcomes.",
                },
                "edit": _allowed_edit,
            },
            {
                "role": "reviewer",
                "payload": {
                    "recommended_action": "promote",
                    "summary": "Research loop materially improved benchmark evidence.",
                    "strengths": ["diagnostic score improved"],
                    "concerns": [],
                },
            },
            {"role": "governor", "payload": {"action": "promote", "reasoning": "Promote it."}},
        ],
        experiment_payloads=[
            _summary_payload(scores=(0.75, 0.8, 0.78), metric_ids=metric_ids),
            _summary_payload(scores=(0.88, 0.9, 0.91), metric_ids=metric_ids),
            _summary_payload(scores=(0.89, 0.91, 0.93), metric_ids=metric_ids),
        ],
    )

    created = engine.init_objective(_init_request(max_revisions=1))
    approved = engine.approve_scenario(created.objective_id)
    baseline_ref = approved.best_benchmark_ref
    completed = engine.run_objective(created.objective_id)

    assert completed.revision_count == 1
    assert completed.best_benchmark_ref != baseline_ref
    assert str(completed.best_benchmark_ref).startswith(str(layout.benchmark_experiments_root))
    assert str(raidar.experiment_calls[1]["summary_path"]).startswith(
        str(layout.research_loop_experiments_root)
    )
    assert raidar.experiment_calls[1]["experiment_kind"] == "research-loop"
    assert raidar.experiment_calls[2]["experiment_kind"] == "benchmark"
    assert Path(completed.scenario_ref or "").parent.name == "v002"
    session_dirs = {call["role"]: call["session_dir"] for call in role_runner.calls}
    assert session_dirs["planner"] != session_dirs["executor"] != session_dirs["reviewer"]

    loop_state = read_json(
        layout.loop_state_path(created.objective_id, _resolved_loop_id(1, "loop-001"))
    )
    diff_path = Path(loop_state["latest_diff_ref"])
    diff_payload = read_json(diff_path)
    assert diff_path.is_file()
    assert diff_payload["changed_files"][0]["path"] == "prompt/task.md"
    reviewer_call = next(call for call in role_runner.calls if call["role"] == "reviewer")
    governor_call = next(call for call in role_runner.calls if call["role"] == "governor")
    assert f"Diff artifact: {diff_path}" in reviewer_call["instruction"]
    assert f"Diff artifact: {diff_path}" in governor_call["instruction"]


def test_promotion_requires_confirmation_before_best_benchmark_changes(tmp_path: Path) -> None:
    metric_ids = ["functional", "acceptance", "verification-stability"]
    engine, _role_runner, _raidar, _layout = _make_engine(
        tmp_path,
        role_scripts=[
            {"role": "designer", "payload": _design_payload()},
            {"role": "critic", "payload": _critic_payload()},
            {
                "role": "planner",
                "payload": {
                    "loops": [
                        {
                            "loop_id": "loop-001",
                            "title": "Prompt refinement",
                            "hypothesis": "Clearer prompt improves results",
                            "instructions": "Tighten prompt wording",
                        }
                    ],
                    "notes": [],
                },
            },
            {
                "role": "executor",
                "payload": {
                    "summary": "Refined the prompt",
                    "changed_files": ["prompt/task.md"],
                    "rationale": "Sharper instruction improves outcomes.",
                },
                "edit": lambda candidate_yaml: (
                    candidate_yaml.parent / "prompt" / "task.md"
                ).write_text("Improved prompt\n", encoding="utf-8"),
            },
            {
                "role": "reviewer",
                "payload": {
                    "recommended_action": "promote",
                    "summary": "Research loop improved the diagnostic score.",
                    "strengths": ["diagnostic score improved"],
                    "concerns": [],
                },
            },
            {"role": "governor", "payload": {"action": "promote", "reasoning": "Promote it."}},
        ],
        experiment_payloads=[
            _summary_payload(scores=(0.75, 0.8, 0.78), metric_ids=metric_ids),
            _summary_payload(scores=(0.88, 0.9, 0.91), metric_ids=metric_ids),
            _summary_payload(
                scores=(0.7, 0.75, 0.76),
                metric_ids=metric_ids,
                performance_pass_rate=0.5,
            ),
        ],
    )
    created = engine.init_objective(_init_request(max_revisions=1))
    approved = engine.approve_scenario(created.objective_id)
    baseline_ref = approved.best_benchmark_ref

    completed = engine.run_objective(created.objective_id)

    assert completed.best_benchmark_ref == baseline_ref
    assert completed.revision_count == 0


def test_mutation_boundary_violation_blocks_loop(tmp_path: Path) -> None:
    metric_ids = ["functional", "acceptance", "verification-stability"]

    def _illegal_edit(candidate_yaml: Path) -> None:
        (candidate_yaml.parent / "notes.txt").write_text("not allowed\n", encoding="utf-8")

    engine, _role_runner, _raidar, layout = _make_engine(
        tmp_path,
        role_scripts=[
            {"role": "designer", "payload": _design_payload()},
            {"role": "critic", "payload": _critic_payload()},
            {
                "role": "planner",
                "payload": {
                    "loops": [
                        {
                            "loop_id": "loop-001",
                            "title": "Prompt refinement",
                            "hypothesis": "Clearer prompt improves results",
                            "instructions": "Tighten prompt wording",
                        }
                    ],
                    "notes": [],
                },
            },
            {
                "role": "executor",
                "payload": {
                    "summary": "Wrote an unsupported file",
                    "changed_files": ["notes.txt"],
                    "rationale": "This should be blocked.",
                },
                "edit": _illegal_edit,
            },
        ],
        experiment_payloads=[_summary_payload(scores=(0.75, 0.8, 0.78), metric_ids=metric_ids)],
    )
    created = engine.init_objective(_init_request())
    engine.approve_scenario(created.objective_id)

    completed = engine.run_objective(created.objective_id)

    loop_state = read_json(
        layout.loop_state_path(created.objective_id, _resolved_loop_id(1, "loop-001"))
    )
    assert completed.best_benchmark_ref is not None
    assert loop_state["status"] == "blocked"
    assert loop_state["stop_reason"] == "illegal-mutation-boundary"


def test_run_namespaces_reused_loop_ids_across_planner_rounds(tmp_path: Path) -> None:
    metric_ids = ["functional", "acceptance", "verification-stability"]

    def _allowed_edit(candidate_yaml: Path) -> None:
        (candidate_yaml.parent / "prompt" / "task.md").write_text(
            "Iterated prompt\n", encoding="utf-8"
        )

    engine, role_runner, _raidar, layout = _make_engine(
        tmp_path,
        role_scripts=[
            {"role": "designer", "payload": _design_payload()},
            {"role": "critic", "payload": _critic_payload()},
            {
                "role": "planner",
                "payload": {
                    "loops": [
                        {
                            "loop_id": "loop-001",
                            "title": "Prompt refinement",
                            "hypothesis": "Iteration one",
                            "instructions": "Tighten prompt wording",
                        }
                    ],
                    "notes": [],
                },
            },
            {
                "role": "executor",
                "payload": {
                    "summary": "Iteration one change",
                    "changed_files": ["prompt/task.md"],
                    "rationale": "Prepare follow-up work.",
                },
                "edit": _allowed_edit,
            },
            {
                "role": "reviewer",
                "payload": {
                    "recommended_action": "spawn_next",
                    "summary": "Continue with another planner round.",
                    "strengths": [],
                    "concerns": [],
                },
            },
            {
                "role": "governor",
                "payload": {"action": "spawn_next", "reasoning": "Schedule another round."},
            },
            {
                "role": "planner",
                "payload": {
                    "loops": [
                        {
                            "loop_id": "loop-001",
                            "title": "Prompt refinement again",
                            "hypothesis": "Iteration two",
                            "instructions": "Tighten prompt wording again",
                        }
                    ],
                    "notes": [],
                },
            },
            {
                "role": "executor",
                "payload": {
                    "summary": "Iteration two change",
                    "changed_files": ["prompt/task.md"],
                    "rationale": "Final iteration.",
                },
                "edit": _allowed_edit,
            },
            {
                "role": "reviewer",
                "payload": {
                    "recommended_action": "discard",
                    "summary": "Stop after the second round.",
                    "strengths": [],
                    "concerns": [],
                },
            },
            {"role": "governor", "payload": {"action": "discard", "reasoning": "Stop now."}},
        ],
        experiment_payloads=[
            _summary_payload(scores=(0.75, 0.8, 0.78), metric_ids=metric_ids),
            _summary_payload(scores=(0.76, 0.81, 0.79), metric_ids=metric_ids),
            _summary_payload(scores=(0.77, 0.82, 0.8), metric_ids=metric_ids),
        ],
    )
    created = engine.init_objective(_init_request(max_revisions=3, max_parallel_loops=1))
    engine.approve_scenario(created.objective_id)

    completed = engine.run_objective(created.objective_id)

    assert completed.status == "completed"
    first_loop_state = read_json(
        layout.loop_state_path(created.objective_id, _resolved_loop_id(1, "loop-001"))
    )
    second_loop_state = read_json(
        layout.loop_state_path(created.objective_id, _resolved_loop_id(2, "loop-001"))
    )
    assert first_loop_state["stop_reason"] == "spawn-next"
    assert second_loop_state["stop_reason"] == "discarded"
    planner_calls = [call for call in role_runner.calls if call["role"] == "planner"]
    assert len(planner_calls) == 2
    assert "unique within this planner response only" in planner_calls[0]["instruction"]
    assert "namespace every loop_id by planner round" in planner_calls[1]["instruction"]


def test_parallel_execution_mode_runs_sibling_loops_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metric_ids = ["functional", "acceptance", "verification-stability"]
    engine, _role_runner, _raidar, _layout = _make_engine(
        tmp_path,
        role_scripts=[
            {"role": "designer", "payload": _design_payload()},
            {"role": "critic", "payload": _critic_payload()},
            {
                "role": "planner",
                "payload": {
                    "loops": [
                        {
                            "loop_id": "loop-001",
                            "title": "One",
                            "hypothesis": "A",
                            "instructions": "A",
                        },
                        {
                            "loop_id": "loop-002",
                            "title": "Two",
                            "hypothesis": "B",
                            "instructions": "B",
                        },
                    ],
                    "notes": [],
                },
            },
        ],
        experiment_payloads=[_summary_payload(scores=(0.75, 0.8, 0.78), metric_ids=metric_ids)],
    )
    created = engine.init_objective(
        _init_request(loop_execution_mode="parallel", max_revisions=1, max_parallel_loops=2)
    )
    engine.approve_scenario(created.objective_id)

    state_lock = threading.Lock()
    gate = threading.Barrier(2)
    active_count = 0
    max_active_count = 0

    def _fake_run_loop(self, objective, loop, round_promotion_state=None):  # noqa: ARG001
        nonlocal active_count, max_active_count
        with state_lock:
            active_count += 1
            max_active_count = max(max_active_count, active_count)
        gate.wait(timeout=1.0)
        time.sleep(0.02)
        with state_lock:
            active_count -= 1
        loop.status = "discarded"
        loop.stop_reason = "discarded"
        return loop

    monkeypatch.setattr(AutoResearchEngine, "_run_loop", _fake_run_loop)

    completed = engine.run_objective(created.objective_id)

    assert completed.status == "completed"
    assert max_active_count == 2


def test_report_includes_loop_execution_mode(tmp_path: Path) -> None:
    engine, _role_runner, _raidar, _layout = _make_engine(
        tmp_path,
        role_scripts=[
            {"role": "designer", "payload": _design_payload()},
            {"role": "critic", "payload": _critic_payload()},
        ],
    )

    objective = engine.init_objective(_init_request(loop_execution_mode="parallel"))

    report = engine.render_objective_report(objective.objective_id)

    assert "- loop_execution_mode: `parallel`" in report
    assert "- max_parallel_loops: `3`" in report


def test_run_uses_objective_repeat_parallel_settings_for_benchmark_and_research(
    tmp_path: Path,
) -> None:
    metric_ids = ["functional", "acceptance", "verification-stability"]

    def _allowed_edit(candidate_yaml: Path) -> None:
        (candidate_yaml.parent / "prompt" / "task.md").write_text(
            "Improved prompt\n",
            encoding="utf-8",
        )

    engine, _role_runner, raidar, _layout = _make_engine(
        tmp_path,
        role_scripts=[
            {"role": "designer", "payload": _design_payload()},
            {"role": "critic", "payload": _critic_payload()},
            {
                "role": "planner",
                "payload": {
                    "loops": [
                        {
                            "loop_id": "loop-001",
                            "title": "Prompt refinement",
                            "hypothesis": "Clearer prompt improves results",
                            "instructions": "Tighten prompt wording",
                        }
                    ],
                    "notes": [],
                },
            },
            {
                "role": "executor",
                "payload": {
                    "summary": "Refined the prompt",
                    "changed_files": ["prompt/task.md"],
                    "rationale": "Sharper instruction improves outcomes.",
                },
                "edit": _allowed_edit,
            },
            {
                "role": "reviewer",
                "payload": {
                    "recommended_action": "promote",
                    "summary": "Research loop materially improved benchmark evidence.",
                    "strengths": ["diagnostic score improved"],
                    "concerns": [],
                },
            },
            {"role": "governor", "payload": {"action": "promote", "reasoning": "Promote it."}},
        ],
        experiment_payloads=[
            _summary_payload(scores=(0.75, 0.8, 0.78), metric_ids=metric_ids),
            _summary_payload(scores=(0.88, 0.9, 0.91), metric_ids=metric_ids),
            _summary_payload(scores=(0.89, 0.91, 0.93), metric_ids=metric_ids),
        ],
    )

    created = engine.init_objective(
        _init_request(
            max_revisions=1,
            benchmark_repeats=6,
            benchmark_repeat_parallel=3,
            research_repeats=4,
            research_repeat_parallel=2,
        )
    )
    engine.approve_scenario(created.objective_id)
    engine.run_objective(created.objective_id)

    benchmark_calls = [
        call for call in raidar.experiment_calls if call["experiment_kind"] == "benchmark"
    ]
    research_calls = [
        call for call in raidar.experiment_calls if call["experiment_kind"] == "research-loop"
    ]

    assert [call["repeats"] for call in benchmark_calls] == [6, 6]
    assert [call["repeat_parallel"] for call in benchmark_calls] == [3, 3]
    assert [call["repeats"] for call in research_calls] == [4]
    assert [call["repeat_parallel"] for call in research_calls] == [2]


def test_parallel_round_allows_only_one_promoted_winner_and_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metric_ids = ["functional", "acceptance", "verification-stability"]
    layout = WorkspaceLayout(tmp_path)
    layout.auto_researcher_root.mkdir(parents=True, exist_ok=True)
    layout.objectives_root.mkdir(parents=True, exist_ok=True)
    layout.scenarios_root.mkdir(parents=True, exist_ok=True)
    layout.benchmark_experiments_root.mkdir(parents=True, exist_ok=True)
    layout.research_loop_experiments_root.mkdir(parents=True, exist_ok=True)

    def _payload_factory(role: str, instruction: str) -> dict[str, Any]:
        if role == "designer":
            return {"payload": _design_payload()}
        if role == "critic":
            return {"payload": _critic_payload()}
        if role == "planner":
            return {
                "payload": {
                    "loops": [
                        {
                            "loop_id": "loop-001",
                            "title": "Prompt refinement one",
                            "hypothesis": "A",
                            "instructions": "Tighten prompt wording",
                        },
                        {
                            "loop_id": "loop-002",
                            "title": "Prompt refinement two",
                            "hypothesis": "B",
                            "instructions": "Tighten prompt wording differently",
                        },
                    ],
                    "notes": [],
                }
            }
        if role == "executor":
            return {
                "payload": {
                    "summary": "Refined the prompt",
                    "changed_files": ["prompt/task.md"],
                    "rationale": "Sharper instruction improves outcomes.",
                },
                "edit": lambda candidate_yaml: (
                    candidate_yaml.parent / "prompt" / "task.md"
                ).write_text(
                    f"Improved prompt for {candidate_yaml.parent.parent.name}\n",
                    encoding="utf-8",
                ),
            }
        if role == "reviewer":
            return {
                "payload": {
                    "recommended_action": "promote",
                    "summary": "Research loop materially improved benchmark evidence.",
                    "strengths": ["diagnostic score improved"],
                    "concerns": [],
                }
            }
        if role == "governor":
            return {"payload": {"action": "promote", "reasoning": "Promote it."}}
        raise AssertionError(f"Unexpected role: {role}\n{instruction}")

    role_runner = DynamicRoleRunner(layout, _payload_factory)
    raidar = FakeRaidar(
        layout,
        experiment_payloads=[
            _summary_payload(scores=(0.75, 0.8, 0.78), metric_ids=metric_ids),
            _summary_payload(scores=(0.88, 0.9, 0.91), metric_ids=metric_ids),
            _summary_payload(scores=(0.9, 0.92, 0.94), metric_ids=metric_ids),
            _summary_payload(scores=(0.91, 0.93, 0.95), metric_ids=metric_ids),
        ],
    )
    engine = AutoResearchEngine(layout=layout, role_runner=role_runner, raidar=raidar)
    original_attempt_promotion = AutoResearchEngine._attempt_promotion

    def _slow_attempt_promotion(self, objective, loop):
        time.sleep(0.05)
        return original_attempt_promotion(self, objective, loop)

    monkeypatch.setattr(AutoResearchEngine, "_attempt_promotion", _slow_attempt_promotion)

    created = engine.init_objective(
        _init_request(loop_execution_mode="parallel", max_revisions=1, max_parallel_loops=2)
    )
    engine.approve_scenario(created.objective_id)
    completed = engine.run_objective(created.objective_id)

    loop_states = [
        read_json(layout.loop_state_path(created.objective_id, _resolved_loop_id(1, "loop-001"))),
        read_json(layout.loop_state_path(created.objective_id, _resolved_loop_id(1, "loop-002"))),
    ]
    benchmark_calls = [
        call for call in raidar.experiment_calls if call["experiment_kind"] == "benchmark"
    ]
    research_calls = [
        call for call in raidar.experiment_calls if call["experiment_kind"] == "research-loop"
    ]

    assert completed.revision_count == 1
    assert len(research_calls) == 2
    assert len(benchmark_calls) == 2
    assert sum(1 for state in loop_states if state["status"] == "promoted") == 1
    assert sum(1 for state in loop_states if state["stop_reason"] == "superseded-by-promotion") == 1


def test_designer_instruction_constrains_metric_ids_for_smoke(tmp_path: Path) -> None:
    engine, _, _, _ = _make_engine(tmp_path, role_scripts=[])
    objective = ObjectiveState(
        objective_id="research-smoke-test",
        created_at_utc="2026-03-24T00:00:00+00:00",
        updated_at_utc="2026-03-24T00:00:00+00:00",
        status="drafting_scenario",
        goal=(
            "Draft and approve a minimal hello-world coding scenario "
            "for autoresearch smoke validation"
        ),
        target_harness="codex-cli",
        target_model="codex/gpt-5.4-mini",
        approval_mode="scenario_only",
        loop_execution_mode="serial",
        max_revisions=1,
        max_parallel_loops=1,
        benchmark_repeats=1,
        benchmark_repeat_parallel=1,
        research_repeats=1,
        research_repeat_parallel=1,
        mutation_surface=["scenario.yaml", "prompt/"],
        role_models={},
    )

    instruction = engine._designer_instruction(  # noqa: SLF001
        objective,
        Path("/tmp/scenario-design.json"),
    )

    assert "Allowed metric ids only:" in instruction
    assert "functional, acceptance, verification-stability" in instruction
    assert "Prefer the default metric set:" in instruction
    assert "Include `starter_files` entries relative to the starter root." in instruction
    assert "Include explicit acceptance coverage with `deterministic_checks`" in instruction
    assert "Always provide a valid `package.json` at the starter root." in instruction
    assert "bun install --lockfile-only" in instruction
    assert "Every `required_commands` entry and every gate command must succeed" in instruction


def test_init_rejects_design_without_materializable_bun_lock(tmp_path: Path) -> None:
    design = _design_payload()
    package_payload = json.loads(design["starter_files"][0]["content"])
    package_payload.pop("devDependencies")
    design["starter_files"][0]["content"] = json.dumps(package_payload)
    engine, _, _, _ = _make_engine(
        tmp_path,
        role_scripts=[{"role": "designer", "payload": design}],
    )

    with pytest.raises(ValidationError, match="dependency or devDependency"):
        engine.init_objective(_init_request())


def test_init_rejects_design_with_failing_starter_baseline_command(tmp_path: Path) -> None:
    design = _design_payload()
    design["required_commands"] = [["bun", "run", "test"]]
    design["gates"] = [{"name": "test", "command": ["bun", "run", "test"]}]
    design["starter_files"].append(
        {
            "path": "test/failing.test.ts",
            "content": (
                'import { expect, test } from "bun:test";\n'
                'test("baseline fails", () => {\n'
                "  expect(1).toBe(2);\n"
                "});\n"
            ),
        }
    )
    engine, _, _, _ = _make_engine(
        tmp_path,
        role_scripts=[{"role": "designer", "payload": design}],
    )

    with pytest.raises(RuntimeError, match="bun run test"):
        engine.init_objective(_init_request())
