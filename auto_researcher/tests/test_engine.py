"""Workflow tests for PI-driven autoresearch orchestration."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from auto_researcher.engine import AutoResearchEngine
from auto_researcher.models import ObjectiveInitRequest
from auto_researcher.pi_rpc import RoleExecution
from auto_researcher.storage import WorkspaceLayout, read_json, read_yaml


def _summary_payload(
    *,
    composite: float,
    quality: float,
    diagnostic: float,
    metric_ids: list[str],
    unscored_count: int = 0,
    validity_rate: float = 1.0,
    performance_pass_rate: float = 1.0,
) -> dict[str, Any]:
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


class FakeRaidar:
    def __init__(
        self,
        layout: WorkspaceLayout,
        experiment_payloads: list[dict[str, Any]] | None = None,
    ) -> None:
        self.layout = layout
        self.experiment_payloads = list(experiment_payloads or [])
        self.experiment_calls: list[dict[str, Any]] = []

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
        (revision_dir / "rules").mkdir(parents=True, exist_ok=True)
        (revision_dir / "prompt").mkdir(parents=True, exist_ok=True)
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
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("Initial prompt\n", encoding="utf-8")
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
        assert scenario_yaml.is_file()

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
        del timeout_sec, repeats, repeat_parallel, experiments_root
        call_index = len(self.experiment_calls) + 1
        if experiment_kind == "benchmark":
            root = self.layout.benchmark_experiments_root
        else:
            root = self.layout.research_loop_experiments_root
        execution_dir = root / f"exp-{call_index:02d}"
        execution_dir.mkdir(parents=True, exist_ok=True)
        payload = self.experiment_payloads.pop(0)
        (execution_dir / "experiment-summary.json").write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        self.experiment_calls.append(
            {
                "experiment_kind": experiment_kind,
                "scenario_yaml": scenario_yaml,
                "harness": harness,
                "model": model,
                "summary_path": execution_dir / "experiment-summary.json",
            }
        )
        return {
            "scenario_path": str(scenario_yaml),
            "scenario_name": scenario_yaml.parent.parent.name,
            "scenario_revision": scenario_yaml.parent.name,
            "summary_path": str(execution_dir / "experiment-summary.json"),
            "report_path": str(execution_dir / "report.md"),
            "experiment_json_path": str(execution_dir / "experiment.json"),
            "runs": [],
            "retries_used": 0,
        }


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
        "notes": ["draft the first scenario"],
    }


def _critic_payload() -> dict[str, Any]:
    return {"decision": "approve", "summary": "Scenario is measurable.", "risks": []}


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
    draft_yaml = Path(objective.draft_scenario_ref or "")
    assert draft_yaml.is_file()
    assert str(draft_yaml).startswith(str(layout.objectives_root))
    assert not any(layout.scenarios_root.rglob("scenario.yaml"))
    assert layout.objective_brief_path(objective.objective_id).is_file()
    assert layout.objective_state_path(objective.objective_id).is_file()
    assert len(role_runner.calls) == 2


def test_approve_promotes_exact_draft_and_seeds_benchmark(tmp_path: Path) -> None:
    metric_ids = ["functional", "acceptance", "verification-stability"]
    engine, _role_runner, raidar, layout = _make_engine(
        tmp_path,
        role_scripts=[
            {"role": "designer", "payload": _design_payload()},
            {"role": "critic", "payload": _critic_payload()},
        ],
        experiment_payloads=[
            _summary_payload(composite=0.75, quality=0.8, diagnostic=0.78, metric_ids=metric_ids)
        ],
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
        experiment_payloads=[
            _summary_payload(composite=0.75, quality=0.8, diagnostic=0.78, metric_ids=metric_ids)
        ],
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
        experiment_payloads=[
            _summary_payload(composite=0.75, quality=0.8, diagnostic=0.78, metric_ids=metric_ids)
        ],
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
            _summary_payload(composite=0.75, quality=0.8, diagnostic=0.78, metric_ids=metric_ids),
            _summary_payload(composite=0.88, quality=0.9, diagnostic=0.91, metric_ids=metric_ids),
            _summary_payload(composite=0.89, quality=0.91, diagnostic=0.93, metric_ids=metric_ids),
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
            _summary_payload(composite=0.75, quality=0.8, diagnostic=0.78, metric_ids=metric_ids),
            _summary_payload(composite=0.88, quality=0.9, diagnostic=0.91, metric_ids=metric_ids),
            _summary_payload(
                composite=0.7,
                quality=0.75,
                diagnostic=0.76,
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
        experiment_payloads=[
            _summary_payload(composite=0.75, quality=0.8, diagnostic=0.78, metric_ids=metric_ids)
        ],
    )
    created = engine.init_objective(_init_request())
    engine.approve_scenario(created.objective_id)

    completed = engine.run_objective(created.objective_id)

    loop_state = read_json(layout.loop_state_path(created.objective_id, "loop-001"))
    assert completed.best_benchmark_ref is not None
    assert loop_state["status"] == "blocked"
    assert loop_state["stop_reason"] == "illegal-mutation-boundary"
