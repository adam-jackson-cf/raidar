"""Objective orchestration engine for PI-driven autoresearch."""

from __future__ import annotations

import concurrent.futures
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from raidar.application.models import (
    ExperimentRunRequest,
    ScenarioCloneRequest,
    ScenarioInitRequest,
)

from .models import (
    ComparisonGuard,
    CriticReview,
    ExecutorMemo,
    GovernorDecision,
    LoopPlan,
    ObjectiveInitRequest,
    ObjectiveState,
    ObjectiveStatus,
    PlannerPlan,
    ResearchLoopState,
    ReviewMemo,
    RoleModelConfig,
    ScenarioDesign,
    StarterFileDesign,
)
from .pi_rpc import RoleExecution, RoleRunner
from .raidar_cli import RaidarClient
from .storage import (
    ScenarioDocumentUpdate,
    WorkspaceLayout,
    copy_tree,
    ensure_dir,
    experiment_summary,
    illegal_mutations,
    load_objective_state,
    read_json,
    scenario_root_from_yaml,
    scenario_timeout_sec,
    slugify,
    snapshot_tree,
    sync_tree,
    update_scenario_document,
    update_scenario_revision,
    utc_now_iso,
    write_compact_tree_diff,
    write_json,
    write_objective_state,
    write_text,
)

DEFAULT_LOCAL_REVISION = "v001"
DESIGN_EXAMPLE_JSON = json.dumps(
    {
        "scenario_slug": "filesystem-safe-slug",
        "scenario_name": "Human-readable name",
        "description": "Typed scenario description",
        "difficulty": "easy|medium|hard",
        "category": "string",
        "timeout_sec": 1800,
        "starter_root": "starter",
        "prompt_entry": "prompt/task.md",
        "prompt_text": "complete draft task prompt",
        "metric_ids": ["functional", "acceptance"],
        "required_commands": [["bun", "run", "lint"]],
        "gates": [{"name": "lint", "command": ["bun", "run", "lint"]}],
        "deterministic_checks": [
            {
                "type": "no_pattern",
                "pattern": "TODO",
                "description": "No TODO markers remain in production files",
            }
        ],
        "requirements": [
            {
                "id": "req-example",
                "description": "Document one measurable requirement",
                "check": {
                    "type": "import_present",
                    "pattern": "Example",
                    "description": "Example marker exists in source",
                },
                "required_test_patterns": ["Example"],
            }
        ],
        "llm_judge_rubric": [
            {
                "criterion": "The implementation satisfies the explicit acceptance criteria.",
                "weight": 1.0,
            }
        ],
        "starter_files": [
            {
                "path": "package.json",
                "content": (
                    '{"name":"smoke-starter","private":true,'
                    '"devDependencies":{"typescript":"^5.8.3"}}'
                ),
            },
        ],
        "notes": ["short notes"],
    }
)


@dataclass(slots=True)
class _RoundPromotionState:
    """Coordinate one winning promotion per planner round."""

    lock: Lock = field(default_factory=Lock)
    promoted_loop_id: str | None = None


@dataclass(slots=True)
class _ScenarioDraftReview:
    """Stable references written during the initial scenario review round."""

    design: ScenarioDesign
    draft_yaml: Path
    critic_path: Path


@dataclass(slots=True)
class _LoopIterationContext:
    """Shared state for one research-loop iteration."""

    objective: ObjectiveState
    loop: ResearchLoopState
    candidate_yaml: Path
    round_promotion_state: _RoundPromotionState | None = None


@dataclass(slots=True)
class AutoResearchEngine:
    """Coordinate objective intake, approval, loop execution, and promotion."""

    layout: WorkspaceLayout
    role_runner: RoleRunner
    raidar: RaidarClient
    _objective_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def init_objective(self, request: ObjectiveInitRequest) -> ObjectiveState:
        self.role_runner.validate()
        objective_id = request.objective_id or self._generate_objective_id(request.goal)
        if self.layout.objective_state_path(objective_id).exists():
            raise RuntimeError(f"Objective already exists: {objective_id}")
        self._ensure_objective_dirs(objective_id)
        objective = self._new_objective_state(request, objective_id)
        self._write_brief(objective)
        self._save_objective(objective)

        design, design_path = self._design_scenario(objective)
        draft_yaml = self._materialize_draft_scenario(objective_id, design)

        critic_path = self.layout.objective_review_dir(objective_id) / "scenario-review.json"
        self._run_role(
            objective=objective,
            role="critic",
            instruction=self._critic_instruction(objective, draft_yaml, design_path, critic_path),
        )
        critic_review = CriticReview.model_validate(read_json(critic_path))
        review = _ScenarioDraftReview(
            design=design,
            draft_yaml=draft_yaml,
            critic_path=critic_path,
        )
        if critic_review.decision == "block":
            return self._record_scenario_review(
                objective,
                review,
                status="blocked",
                stop_reason="critic_blocked",
            )

        if critic_review.decision == "revise":
            return self._record_scenario_review(
                objective,
                review,
                status="drafting_scenario",
                stop_reason="critic_requested_revision",
            )

        return self._record_scenario_review(
            objective,
            review,
            status="awaiting_scenario_approval",
            stop_reason=None,
        )

    def _new_objective_state(
        self,
        request: ObjectiveInitRequest,
        objective_id: str,
    ) -> ObjectiveState:
        return ObjectiveState(
            objective_id=objective_id,
            created_at_utc=utc_now_iso(),
            updated_at_utc=utc_now_iso(),
            status="drafting_scenario",
            goal=request.goal,
            target_harness=request.target_harness,
            target_model=request.target_model,
            approval_mode=request.approval_mode,
            loop_execution_mode=request.loop_execution_mode,
            max_revisions=request.max_revisions,
            max_parallel_loops=request.max_parallel_loops,
            benchmark_repeats=request.benchmark_repeats,
            benchmark_repeat_parallel=request.benchmark_repeat_parallel,
            research_repeats=request.research_repeats,
            research_repeat_parallel=request.research_repeat_parallel,
            mutation_surface=list(request.mutation_surface),
            role_models=self._resolved_role_models(request.role_models),
        )

    def _design_scenario(self, objective: ObjectiveState) -> tuple[ScenarioDesign, Path]:
        design_path = (
            self.layout.objective_plan_dir(objective.objective_id) / "scenario-design.json"
        )
        self._run_role(
            objective=objective,
            role="designer",
            instruction=self._designer_instruction(objective, design_path),
        )
        return ScenarioDesign.model_validate(read_json(design_path)), design_path

    def _materialize_draft_scenario(self, objective_id: str, design: ScenarioDesign) -> Path:
        draft_root = self.layout.objective_draft_root(objective_id, design.scenario_slug)
        self.raidar.scenario_init(
            ScenarioInitRequest(
                path=draft_root,
                name=design.scenario_name,
                scenario_revision=DEFAULT_LOCAL_REVISION,
                starter_root=design.starter_root,
                prompt_entry=design.prompt_entry,
                difficulty=design.difficulty,
                category=design.category,
                timeout_sec=design.timeout_sec,
            )
        )
        draft_yaml = draft_root / DEFAULT_LOCAL_REVISION / "scenario.yaml"
        update_scenario_document(
            draft_yaml,
            ScenarioDocumentUpdate(
                name=design.scenario_name,
                description=design.description,
                difficulty=design.difficulty,
                category=design.category,
                timeout_sec=design.timeout_sec,
                starter_root=design.starter_root,
                prompt_entry=design.prompt_entry,
                metric_ids=design.metric_ids,
                required_commands=design.required_commands,
                gates=[gate.model_dump(mode="json") for gate in design.gates],
                deterministic_checks=design.deterministic_checks,
                requirements=design.requirements,
                llm_judge_rubric=design.llm_judge_rubric,
            ),
        )
        prompt_path = draft_root / DEFAULT_LOCAL_REVISION / design.prompt_entry
        write_text(prompt_path, design.prompt_text.rstrip() + "\n")
        starter_root = ensure_dir(draft_root / DEFAULT_LOCAL_REVISION / design.starter_root)
        self._write_starter_files(starter_root, design.starter_files)
        self._materialize_starter_lockfile(starter_root)
        self._validate_starter_baseline_commands(design, starter_root)
        self.raidar.scenario_validate(scenario_yaml=draft_yaml)
        return draft_yaml

    def _record_scenario_review(
        self,
        objective: ObjectiveState,
        review: _ScenarioDraftReview,
        *,
        status: ObjectiveStatus,
        stop_reason: str | None,
    ) -> ObjectiveState:
        objective.status = status
        objective.stop_reason = stop_reason
        objective.updated_at_utc = utc_now_iso()
        objective.scenario_slug = review.design.scenario_slug
        objective.scenario_name = review.design.scenario_name
        objective.draft_scenario_ref = str(review.draft_yaml)
        objective.frozen_metric_ids = list(review.design.metric_ids)
        objective.latest_scenario_review_ref = str(review.critic_path)
        self._save_objective(objective)
        self._write_report(objective)
        return objective

    def approve_scenario(self, objective_id: str) -> ObjectiveState:
        objective = self._load_objective(objective_id)
        if objective.status != "awaiting_scenario_approval":
            raise RuntimeError(
                f"Objective must be awaiting scenario approval, got {objective.status}."
            )
        if objective.draft_scenario_ref is None or objective.scenario_slug is None:
            raise RuntimeError("Objective is missing draft scenario metadata.")
        draft_yaml = Path(objective.draft_scenario_ref)
        draft_root = scenario_root_from_yaml(draft_yaml)
        canonical_root = self.layout.scenarios_root / objective.scenario_slug
        scenario_yaml = canonical_root / draft_yaml.parent.name / "scenario.yaml"
        did_copy = False

        if canonical_root.exists() and scenario_yaml.is_file():
            did_copy = False
        else:
            if canonical_root.exists():
                self._delete_path(canonical_root)
            ensure_dir(canonical_root.parent)
            copy_tree(draft_root, canonical_root)
            did_copy = True

        timeout_sec = scenario_timeout_sec(scenario_yaml)
        try:
            baseline = self.raidar.experiment_run(
                ExperimentRunRequest(
                    scenario=scenario_yaml,
                    harness=objective.target_harness,
                    model=objective.target_model,
                    timeout=timeout_sec,
                    repeats=objective.benchmark_repeats,
                    repeat_parallel=objective.benchmark_repeat_parallel,
                    rerun_unscored=1,
                    experiment_kind="benchmark",
                )
            )
        except Exception:
            if did_copy:
                self._delete_path(canonical_root)
            raise

        objective.status = "active"
        objective.updated_at_utc = utc_now_iso()
        objective.stop_reason = None
        objective.scenario_ref = str(scenario_yaml)
        objective.best_benchmark_ref = str(baseline.summary_path)
        self._save_objective(objective)
        self._write_report(objective)
        return objective

    def run_objective(self, objective_id: str) -> ObjectiveState:
        objective = self._load_objective(objective_id)
        if objective.status != "active":
            raise RuntimeError(f"Objective must be active before run, got {objective.status}.")
        if objective.scenario_ref is None or objective.best_benchmark_ref is None:
            raise RuntimeError("Objective is missing approved scenario or benchmark baseline.")

        max_plan_rounds = max(1, objective.max_revisions * objective.max_parallel_loops)
        while objective.status == "active":
            if self._should_stop_planning(objective, max_plan_rounds):
                break
            promoted, requested_follow_up = self._execute_plan_round(objective)
            objective = self._load_objective(objective.objective_id)
            if objective.status != "active":
                break
            if promoted:
                continue
            if not requested_follow_up:
                objective.status = "completed"
                objective.stop_reason = "no-further-loop-actions"
                break

        objective.updated_at_utc = utc_now_iso()
        self._save_objective(objective)
        self._write_report(objective)
        return objective

    def objective_status(self, objective_id: str) -> dict[str, Any]:
        objective = self._load_objective(objective_id)
        loops = self._loop_states(objective_id)
        return {
            "objective_id": objective.objective_id,
            "status": objective.status,
            "goal": objective.goal,
            "scenario_ref": objective.scenario_ref,
            "draft_scenario_ref": objective.draft_scenario_ref,
            "best_benchmark_ref": objective.best_benchmark_ref,
            "revision_count": objective.revision_count,
            "loop_execution_mode": objective.loop_execution_mode,
            "max_revisions": objective.max_revisions,
            "max_parallel_loops": objective.max_parallel_loops,
            "benchmark_repeats": objective.benchmark_repeats,
            "benchmark_repeat_parallel": objective.benchmark_repeat_parallel,
            "research_repeats": objective.research_repeats,
            "research_repeat_parallel": objective.research_repeat_parallel,
            "loop_statuses": {loop.loop_id: loop.status for loop in loops},
            "stop_reason": objective.stop_reason,
        }

    def render_objective_report(self, objective_id: str) -> str:
        objective = self._load_objective(objective_id)
        loops = self._loop_states(objective_id)
        lines = [
            f"# Objective {objective.objective_id}",
            "",
            f"- status: `{objective.status}`",
            f"- goal: {objective.goal}",
            f"- target: `{objective.target_harness}` / `{objective.target_model}`",
            f"- loop_execution_mode: `{objective.loop_execution_mode}`",
            f"- max_parallel_loops: `{objective.max_parallel_loops}`",
            f"- benchmark_repeat_parallel: `{objective.benchmark_repeat_parallel}`",
            f"- research_repeat_parallel: `{objective.research_repeat_parallel}`",
            "- scenario_ref: "
            f"`{objective.scenario_ref or objective.draft_scenario_ref or '(none)'}`",
            f"- best_benchmark_ref: `{objective.best_benchmark_ref or '(none)'}`",
            f"- revisions: `{objective.revision_count}` / `{objective.max_revisions}`",
            f"- stop_reason: `{objective.stop_reason or '(active)'}`",
        ]
        if objective.frozen_metric_ids:
            lines.append(f"- frozen_metrics: `{', '.join(objective.frozen_metric_ids)}`")
        if loops:
            lines.extend(["", "## Research Loops"])
            for loop in loops:
                lines.append(
                    f"- {loop.loop_id}: `{loop.status}` iteration={loop.iteration} "
                    f"candidate=`{loop.candidate_scenario_ref}`"
                )
        return "\n".join(lines) + "\n"

    def _planner_round(self, objective: ObjectiveState) -> PlannerPlan:
        objective.planner_round += 1
        objective.updated_at_utc = utc_now_iso()
        self._save_objective(objective)
        plan_path = (
            self.layout.objective_plan_dir(objective.objective_id)
            / f"planner-round-{objective.planner_round:02d}.json"
        )
        self._run_role(
            objective=objective,
            role="planner",
            instruction=self._planner_instruction(objective, plan_path),
        )
        plan = PlannerPlan.model_validate(read_json(plan_path))
        seen_ids: set[str] = set()
        for loop in plan.loops:
            if loop.loop_id in seen_ids:
                raise RuntimeError(f"Planner returned duplicate loop id: {loop.loop_id}")
            seen_ids.add(loop.loop_id)
        if len(plan.loops) > objective.max_parallel_loops:
            raise RuntimeError("Planner exceeded max_parallel_loops constraint.")
        return plan

    def _should_stop_planning(self, objective: ObjectiveState, max_plan_rounds: int) -> bool:
        if objective.revision_count >= objective.max_revisions:
            objective.status = "completed"
            objective.stop_reason = "revision-budget-exhausted"
            return True
        if objective.planner_round >= max_plan_rounds:
            objective.status = "completed"
            objective.stop_reason = "planner-round-budget-exhausted"
            return True
        return False

    def _execute_plan_round(self, objective: ObjectiveState) -> tuple[bool, bool]:
        plan = self._planner_round(objective)
        if not plan.loops:
            objective.status = "completed"
            objective.stop_reason = "planner-returned-no-loops"
            self._save_objective(objective)
            return False, False

        planned_loops = plan.loops[: objective.max_parallel_loops]
        if objective.loop_execution_mode == "parallel":
            return self._execute_plan_round_parallel(objective, planned_loops)
        return self._execute_plan_round_serial(objective, planned_loops)

    def _execute_plan_round_serial(
        self,
        objective: ObjectiveState,
        planned_loops: list[LoopPlan],
    ) -> tuple[bool, bool]:
        promoted = False
        requested_follow_up = False
        for loop_plan in planned_loops:
            loop = self._create_loop(objective, loop_plan)
            loop = self._run_loop(objective, loop)
            if loop.status == "promoted":
                promoted = True
                self._mark_superseded_loops(
                    objective,
                    planned_loops=planned_loops,
                    executed_loop_id=loop.loop_id,
                )
                break
            if loop.stop_reason == "spawn-next":
                requested_follow_up = True
            objective = self._load_objective(objective.objective_id)
            if objective.status != "active":
                break
        return promoted, requested_follow_up

    def _execute_plan_round_parallel(
        self,
        objective: ObjectiveState,
        planned_loops: list[LoopPlan],
    ) -> tuple[bool, bool]:
        promotion_state = _RoundPromotionState()
        loop_results: dict[str, ResearchLoopState] = {}
        max_workers = max(1, min(objective.max_parallel_loops, len(planned_loops)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map: dict[concurrent.futures.Future[ResearchLoopState], str] = {}
            for loop_plan in planned_loops:
                loop = self._create_loop(objective, loop_plan)
                future = executor.submit(
                    self._run_loop,
                    objective.model_copy(deep=True),
                    loop,
                    promotion_state,
                )
                future_map[future] = loop.loop_id
            for future in concurrent.futures.as_completed(future_map):
                loop = future.result()
                loop_results[loop.loop_id] = loop

        promoted_loop = next(
            (loop for loop in loop_results.values() if loop.status == "promoted"),
            None,
        )
        if promoted_loop is not None:
            self._mark_superseded_loops(
                objective,
                planned_loops=planned_loops,
                executed_loop_id=promoted_loop.loop_id,
            )
            return True, any(
                loop.stop_reason == "spawn-next"
                for loop in loop_results.values()
                if loop.loop_id != promoted_loop.loop_id
            )
        return False, any(loop.stop_reason == "spawn-next" for loop in loop_results.values())

    def _create_loop(self, objective: ObjectiveState, loop_plan: LoopPlan) -> ResearchLoopState:
        if objective.scenario_ref is None or objective.scenario_slug is None:
            raise RuntimeError("Objective is missing approved scenario metadata.")
        resolved_loop_id = self._resolved_loop_id(objective, loop_plan.loop_id)
        loop_root = self.layout.loop_root(objective.objective_id, resolved_loop_id)
        if loop_root.exists():
            raise RuntimeError(f"Loop already exists: {resolved_loop_id}")
        candidate_root = self.layout.loop_candidate_root(
            objective.objective_id, resolved_loop_id, objective.scenario_slug
        )
        copy_tree(scenario_root_from_yaml(Path(objective.scenario_ref)), candidate_root)
        current_revision = Path(objective.scenario_ref).parent.name
        candidate_yaml = candidate_root / current_revision / "scenario.yaml"
        loop = ResearchLoopState(
            loop_id=resolved_loop_id,
            objective_id=objective.objective_id,
            title=loop_plan.title,
            hypothesis=loop_plan.hypothesis,
            instructions=loop_plan.instructions,
            created_at_utc=utc_now_iso(),
            updated_at_utc=utc_now_iso(),
            status="queued",
            iteration=1,
            max_iterations=objective.max_revisions,
            candidate_scenario_ref=str(candidate_yaml),
        )
        self._save_loop(loop)
        return loop

    def _run_loop(
        self,
        objective: ObjectiveState,
        loop: ResearchLoopState,
        round_promotion_state: _RoundPromotionState | None = None,
    ) -> ResearchLoopState:
        while True:
            iteration = _LoopIterationContext(
                objective=objective,
                loop=loop,
                candidate_yaml=Path(loop.candidate_scenario_ref),
                round_promotion_state=round_promotion_state,
            )
            if self._run_executor_iteration(iteration):
                return loop
            self._run_research_iteration(iteration)
            decision = self._run_review_iteration(iteration)
            should_continue = self._apply_governor_decision(iteration, decision)
            if should_continue:
                continue
            break

        loop.updated_at_utc = utc_now_iso()
        self._save_loop(loop)
        self._write_report(self._load_objective(objective.objective_id))
        return loop

    def _run_executor_iteration(self, iteration: _LoopIterationContext) -> bool:
        loop = iteration.loop
        objective = iteration.objective
        candidate_revision_dir = iteration.candidate_yaml.parent
        loop.status = "running"
        loop.updated_at_utc = utc_now_iso()
        self._save_loop(loop)

        snapshot_dir = (
            self.layout.loop_snapshots_dir(objective.objective_id, loop.loop_id)
            / f"iteration-{loop.iteration:02d}-before"
        )
        before = snapshot_tree(candidate_revision_dir)
        copy_tree(candidate_revision_dir, snapshot_dir)
        executor_path = (
            self.layout.loop_root(objective.objective_id, loop.loop_id)
            / "execution"
            / f"iteration-{loop.iteration:02d}.json"
        )
        execution = self._run_role(
            objective=objective,
            role="executor",
            instruction=self._executor_instruction(objective, loop, executor_path),
        )
        loop.session_paths["executor"] = str(execution.session_dir)
        ExecutorMemo.model_validate(read_json(executor_path))
        after = snapshot_tree(candidate_revision_dir)
        diff_path = (
            self.layout.loop_diffs_dir(objective.objective_id, loop.loop_id)
            / f"iteration-{loop.iteration:02d}.json"
        )
        write_compact_tree_diff(snapshot_dir, candidate_revision_dir, diff_path)
        loop.latest_diff_ref = str(diff_path)
        if not illegal_mutations(before, after, objective.mutation_surface):
            return False
        loop.status = "blocked"
        loop.stop_reason = "illegal-mutation-boundary"
        self._save_loop(loop)
        self._write_report(objective)
        return True

    def _run_research_iteration(self, iteration: _LoopIterationContext) -> None:
        objective = iteration.objective
        loop = iteration.loop
        research_result = self.raidar.experiment_run(
            ExperimentRunRequest(
                scenario=iteration.candidate_yaml,
                harness=objective.target_harness,
                model=objective.target_model,
                timeout=scenario_timeout_sec(iteration.candidate_yaml),
                repeats=objective.research_repeats,
                repeat_parallel=objective.research_repeat_parallel,
                rerun_unscored=1,
                experiment_kind="research-loop",
            )
        )
        loop.latest_research_summary_ref = str(research_result.summary_path)
        loop.status = "review_pending"
        loop.updated_at_utc = utc_now_iso()
        self._save_loop(loop)

    def _run_review_iteration(self, iteration: _LoopIterationContext) -> GovernorDecision:
        objective = iteration.objective
        loop = iteration.loop
        review_path = (
            self.layout.loop_root(objective.objective_id, loop.loop_id)
            / "reviews"
            / f"iteration-{loop.iteration:02d}.json"
        )
        review_execution = self._run_role(
            objective=objective,
            role="reviewer",
            instruction=self._reviewer_instruction(objective, loop, review_path),
        )
        loop.session_paths["reviewer"] = str(review_execution.session_dir)
        ReviewMemo.model_validate(read_json(review_path))
        loop.latest_review_ref = str(review_path)

        governor_path = (
            self.layout.loop_root(objective.objective_id, loop.loop_id)
            / "governor"
            / f"iteration-{loop.iteration:02d}.json"
        )
        governor_execution = self._run_role(
            objective=objective,
            role="governor",
            instruction=self._governor_instruction(objective, loop, review_path, governor_path),
        )
        loop.session_paths["governor"] = str(governor_execution.session_dir)
        decision = GovernorDecision.model_validate(read_json(governor_path))
        loop.latest_governor_ref = str(governor_path)
        return decision

    def _apply_governor_decision(
        self,
        iteration: _LoopIterationContext,
        decision: GovernorDecision,
    ) -> bool:
        objective = iteration.objective
        loop = iteration.loop
        if decision.action == "iterate":
            return self._continue_loop_iteration(loop=loop, candidate_yaml=iteration.candidate_yaml)
        if decision.action == "promote":
            return self._handle_promotion_decision(
                objective=objective,
                loop=loop,
                round_promotion_state=iteration.round_promotion_state,
            )
        if decision.action == "discard":
            loop.status = "discarded"
            loop.stop_reason = "discarded"
            return False
        if decision.action == "spawn_next":
            loop.status = "completed"
            loop.stop_reason = "spawn-next"
            return False
        if decision.action == "stop":
            self._complete_objective(objective.objective_id, "governor-stop")
            loop.status = "completed"
            loop.stop_reason = "governor-stop"
            return False
        raise RuntimeError(f"Unsupported governor action: {decision.action}")

    def _continue_loop_iteration(self, *, loop: ResearchLoopState, candidate_yaml: Path) -> bool:
        if loop.iteration >= loop.max_iterations:
            loop.status = "completed"
            loop.stop_reason = "iteration-budget-exhausted"
            return False
        clone = self.raidar.scenario_clone_revision(
            ScenarioCloneRequest(
                path=scenario_root_from_yaml(candidate_yaml),
                from_revision=candidate_yaml.parent.name,
            )
        )
        loop.iteration += 1
        loop.status = "iterating"
        loop.candidate_scenario_ref = str(clone.target_scenario_yaml)
        loop.updated_at_utc = utc_now_iso()
        self._save_loop(loop)
        return True

    def _handle_promotion_decision(
        self,
        *,
        objective: ObjectiveState,
        loop: ResearchLoopState,
        round_promotion_state: _RoundPromotionState | None,
    ) -> bool:
        if round_promotion_state is not None:
            with round_promotion_state.lock:
                if round_promotion_state.promoted_loop_id is not None:
                    loop.status = "completed"
                    loop.stop_reason = "superseded-by-promotion"
                    return False
                if self._attempt_promotion(objective, loop):
                    round_promotion_state.promoted_loop_id = loop.loop_id
                    loop.status = "promoted"
                    loop.stop_reason = "promoted"
                    return False
        elif self._attempt_promotion(objective, loop):
            loop.status = "promoted"
            loop.stop_reason = "promoted"
            return False
        loop.status = "completed"
        loop.stop_reason = "promotion-guard-rejected"
        return False

    def _attempt_promotion(self, objective: ObjectiveState, loop: ResearchLoopState) -> bool:
        with self._objective_lock:
            latest_objective = self._load_objective(objective.objective_id)
            if latest_objective.best_benchmark_ref is None or latest_objective.scenario_ref is None:
                raise RuntimeError("Objective promotion requires an approved benchmark baseline.")
            if loop.latest_research_summary_ref is None:
                raise RuntimeError("Cannot promote loop without a research summary.")
            if latest_objective.status != "active":
                return False

            baseline = experiment_summary(Path(latest_objective.best_benchmark_ref))
            research = experiment_summary(Path(loop.latest_research_summary_ref))
            research_guard = self._promotion_guard(
                research,
                baseline,
                latest_objective.frozen_metric_ids,
            )
            promotion_guard_path = (
                self.layout.loop_root(objective.objective_id, loop.loop_id) / "promotion-guard.json"
            )
            write_json(
                promotion_guard_path,
                research_guard.model_dump(mode="json"),
            )
            if not research_guard.passed:
                return False

            confirmation = self.raidar.experiment_run(
                ExperimentRunRequest(
                    scenario=Path(loop.candidate_scenario_ref),
                    harness=latest_objective.target_harness,
                    model=latest_objective.target_model,
                    timeout=scenario_timeout_sec(Path(loop.candidate_scenario_ref)),
                    repeats=latest_objective.benchmark_repeats,
                    repeat_parallel=latest_objective.benchmark_repeat_parallel,
                    rerun_unscored=1,
                    experiment_kind="benchmark",
                )
            )
            confirmation_summary_ref = str(confirmation.summary_path)
            confirmation_guard = self._promotion_guard(
                experiment_summary(Path(confirmation_summary_ref)),
                baseline,
                latest_objective.frozen_metric_ids,
            )
            write_json(
                self.layout.loop_root(objective.objective_id, loop.loop_id)
                / "confirmation-guard.json",
                confirmation_guard.model_dump(mode="json"),
            )
            if not confirmation_guard.passed:
                return False

            promoted_yaml = self._promote_candidate_revision(latest_objective, loop)
            latest_objective.scenario_ref = str(promoted_yaml)
            latest_objective.best_benchmark_ref = confirmation_summary_ref
            latest_objective.revision_count += 1
            latest_objective.updated_at_utc = utc_now_iso()
            self._save_objective(latest_objective)
            loop.promoted_benchmark_ref = confirmation_summary_ref
            self._save_loop(loop)
            return True

    def _promote_candidate_revision(
        self, objective: ObjectiveState, loop: ResearchLoopState
    ) -> Path:
        current_yaml = Path(objective.scenario_ref or "")
        candidate_yaml = Path(loop.candidate_scenario_ref)
        clone = self.raidar.scenario_clone_revision(
            ScenarioCloneRequest(
                path=scenario_root_from_yaml(current_yaml),
                from_revision=current_yaml.parent.name,
            )
        )
        target_revision_dir = clone.target_scenario_yaml.parent
        sync_tree(candidate_yaml.parent, target_revision_dir)
        promoted_yaml = target_revision_dir / "scenario.yaml"
        update_scenario_revision(promoted_yaml, clone.target_revision)
        self.raidar.scenario_validate(scenario_yaml=promoted_yaml)
        return promoted_yaml

    def _promotion_guard(
        self,
        candidate: dict[str, Any],
        baseline: dict[str, Any],
        metric_ids: list[str],
    ) -> ComparisonGuard:
        candidate_agg = self._aggregate_summary(candidate)
        baseline_agg = self._aggregate_summary(baseline)
        blocking_reasons = self._promotion_blocking_reasons(
            candidate_agg,
            baseline_agg,
            metric_ids,
        )
        improved_dimensions = self._promotion_improvements(candidate_agg, baseline_agg)

        return ComparisonGuard(
            passed=not blocking_reasons and bool(improved_dimensions),
            improved_dimensions=improved_dimensions,
            blocking_reasons=blocking_reasons,
        )

    @staticmethod
    def _aggregate_summary(result: dict[str, Any]) -> dict[str, Any]:
        return dict(result.get("aggregate") or {})

    def _promotion_blocking_reasons(
        self,
        candidate_agg: dict[str, Any],
        baseline_agg: dict[str, Any],
        metric_ids: list[str],
    ) -> list[str]:
        checks = (
            ("candidate-has-unscored-runs", int(candidate_agg.get("unscored_count") or 0) != 0),
            ("candidate-validity-rate-below-1.0", self._rate(candidate_agg, "validity_rate") < 1.0),
            (
                "candidate-validity-regressed",
                self._rate(candidate_agg, "validity_rate")
                < self._rate(baseline_agg, "validity_rate"),
            ),
            (
                "candidate-performance-gates-regressed",
                self._rate(candidate_agg, "performance_pass_rate")
                < self._rate(baseline_agg, "performance_pass_rate"),
            ),
        )
        blocking_reasons = [reason for reason, is_blocked in checks if is_blocked]
        blocking_reasons.extend(
            f"metric-regressed:{metric_id}"
            for metric_id in metric_ids
            if self._metric_pass_rate(candidate_agg, metric_id)
            < self._metric_pass_rate(baseline_agg, metric_id)
        )
        return blocking_reasons

    def _promotion_improvements(
        self,
        candidate_agg: dict[str, Any],
        baseline_agg: dict[str, Any],
    ) -> list[str]:
        return [
            score_name
            for score_name in ("composite_score", "quality_score", "diagnostic_score")
            if self._score_mean(candidate_agg, score_name)
            > self._score_mean(baseline_agg, score_name)
        ]

    @staticmethod
    def _rate(aggregate: dict[str, Any], key: str) -> float:
        return float(aggregate.get(key) or 0.0)

    @staticmethod
    def _metric_pass_rate(aggregate: dict[str, Any], metric_id: str) -> float:
        outcomes = dict(aggregate.get("metric_outcomes") or {})
        return float((dict(outcomes.get(metric_id) or {})).get("pass_rate") or 0.0)

    @staticmethod
    def _score_mean(aggregate: dict[str, Any], score_name: str) -> float:
        return float((dict(aggregate.get(score_name) or {})).get("mean") or 0.0)

    def _mark_superseded_loops(
        self, objective: ObjectiveState, *, planned_loops: list[LoopPlan], executed_loop_id: str
    ) -> None:
        for loop_plan in planned_loops:
            resolved_loop_id = self._resolved_loop_id(objective, loop_plan.loop_id)
            if resolved_loop_id == executed_loop_id:
                continue
            state_path = self.layout.loop_state_path(objective.objective_id, resolved_loop_id)
            if not state_path.exists():
                continue
            loop = ResearchLoopState.model_validate(read_json(state_path))
            if loop.status in {"queued", "running", "review_pending", "iterating"}:
                loop.status = "completed"
                loop.stop_reason = "superseded-by-promotion"
                loop.updated_at_utc = utc_now_iso()
                self._save_loop(loop)

    def _run_role(self, *, objective: ObjectiveState, role: str, instruction: str) -> RoleExecution:
        execution = self.role_runner.run_role(
            objective_id=objective.objective_id,
            role=role,
            instruction=instruction,
            model=self._role_model(objective, role),
        )
        return execution

    def _delete_path(self, path: Path) -> None:
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    def _role_model(self, objective: ObjectiveState, role: str) -> RoleModelConfig:
        model = objective.role_models.get(role)
        if model is None:
            raise RuntimeError(f"Missing role model assignment for role: {role}")
        return model

    def _resolved_role_models(
        self, overrides: dict[str, RoleModelConfig]
    ) -> dict[str, RoleModelConfig]:
        default_model = overrides.get("__default__", RoleModelConfig())
        resolved: dict[str, RoleModelConfig] = {}
        for role in ("designer", "critic", "planner", "executor", "reviewer", "governor"):
            resolved[role] = overrides.get(role, default_model)
        return resolved

    def _ensure_objective_dirs(self, objective_id: str) -> None:
        ensure_dir(self.layout.objective_root(objective_id))
        ensure_dir(self.layout.objective_plan_dir(objective_id))
        ensure_dir(self.layout.objective_review_dir(objective_id))
        ensure_dir(self.layout.objective_loops_root(objective_id))
        ensure_dir(self.layout.objectives_root)

    def _write_brief(self, objective: ObjectiveState) -> None:
        content = "\n".join(
            [
                f"# Objective {objective.objective_id}",
                "",
                objective.goal,
                "",
                "## Constraints",
                f"- approval_mode: `{objective.approval_mode}`",
                f"- loop_execution_mode: `{objective.loop_execution_mode}`",
                f"- max_revisions: `{objective.max_revisions}`",
                f"- max_parallel_loops: `{objective.max_parallel_loops}`",
                f"- benchmark_repeats: `{objective.benchmark_repeats}`",
                f"- benchmark_repeat_parallel: `{objective.benchmark_repeat_parallel}`",
                f"- research_repeats: `{objective.research_repeats}`",
                f"- research_repeat_parallel: `{objective.research_repeat_parallel}`",
                f"- mutation_surface: `{', '.join(objective.mutation_surface)}`",
            ]
        )
        write_text(self.layout.objective_brief_path(objective.objective_id), content + "\n")

    def _write_report(self, objective: ObjectiveState) -> None:
        write_text(
            self.layout.objective_report_path(objective.objective_id),
            self.render_objective_report(objective.objective_id),
        )

    def _save_objective(self, objective: ObjectiveState) -> None:
        objective.updated_at_utc = utc_now_iso()
        write_objective_state(self.layout.objective_state_path(objective.objective_id), objective)

    def _load_objective(self, objective_id: str) -> ObjectiveState:
        return load_objective_state(self.layout.objective_state_path(objective_id))

    def _save_loop(self, loop: ResearchLoopState) -> None:
        write_json(
            self.layout.loop_state_path(loop.objective_id, loop.loop_id),
            loop.model_dump(mode="json", exclude_none=True),
        )

    def _loop_states(self, objective_id: str) -> list[ResearchLoopState]:
        loops_root = self.layout.objective_loops_root(objective_id)
        if not loops_root.exists():
            return []
        states: list[ResearchLoopState] = []
        for state_path in sorted(loops_root.glob("*/state.json")):
            states.append(ResearchLoopState.model_validate(read_json(state_path)))
        return states

    def _generate_objective_id(self, goal: str) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ").lower()
        return f"{slugify(goal)}-{stamp}"

    def _complete_objective(self, objective_id: str, stop_reason: str) -> None:
        with self._objective_lock:
            objective = self._load_objective(objective_id)
            objective.status = "completed"
            objective.stop_reason = stop_reason
            self._save_objective(objective)

    def _resolved_loop_id(self, objective: ObjectiveState, raw_loop_id: str) -> str:
        if raw_loop_id in {"", ".", ".."} or "/" in raw_loop_id:
            raise RuntimeError(f"Planner returned invalid loop id: {raw_loop_id}")
        return f"round-{objective.planner_round:02d}__{raw_loop_id}"

    def _write_starter_files(
        self,
        starter_root: Path,
        starter_files: list[StarterFileDesign],
    ) -> None:
        for starter_file in starter_files:
            relative_path = Path(starter_file.path)
            if (
                relative_path.is_absolute()
                or ".." in relative_path.parts
                or relative_path.parts == ()
            ):
                raise RuntimeError(
                    f"Designer returned invalid starter file path: {starter_file.path}"
                )
            output_path = starter_root / relative_path
            ensure_dir(output_path.parent)
            write_text(output_path, starter_file.content)

    def _materialize_starter_lockfile(self, starter_root: Path) -> None:
        lockfile_path = starter_root / "bun.lock"
        install = subprocess.run(
            ["bun", "install", "--lockfile-only"],
            cwd=starter_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if install.returncode != 0:
            output = (install.stdout + "\n" + install.stderr).strip()[:4000]
            raise RuntimeError(
                f"Failed to generate starter bun.lock with `bun install --lockfile-only`.\n{output}"
            )
        if not lockfile_path.is_file():
            raise RuntimeError(
                "Starter package.json did not produce bun.lock. Declare at least one dependency "
                "or devDependency before scenario approval."
            )

    def _validate_starter_baseline_commands(
        self,
        design: ScenarioDesign,
        starter_root: Path,
    ) -> None:
        seen_commands: set[tuple[str, ...]] = set()
        starter_commands = [*design.required_commands, *(gate.command for gate in design.gates)]
        for command in starter_commands:
            command_key = tuple(command)
            if command_key in seen_commands:
                continue
            seen_commands.add(command_key)
            result = subprocess.run(
                command,
                cwd=starter_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                output = (result.stdout + "\n" + result.stderr).strip()[:4000]
                rendered = " ".join(command)
                raise RuntimeError(
                    "Starter baseline command failed before scenario approval: "
                    f"`{rendered}` exited {result.returncode}\n{output}"
                )

    def _designer_instruction(self, objective: ObjectiveState, output_path: Path) -> str:
        header_lines = [
            f"Objective goal: {objective.goal}",
            f"Target harness: {objective.target_harness}",
            f"Target model: {objective.target_model}",
            f"Frozen metric count target: {max(1, len(objective.frozen_metric_ids) or 5)}",
            f"Write JSON to: {output_path}",
        ]
        guidance_lines = [
            "Allowed metric ids only: functional, acceptance, verification-stability, "
            "execution-validity, resource-efficiency, test-coverage, "
            "requirements-coverage, llm-judge, visual-regression.",
            "Include `starter_files` entries relative to the starter root.",
            "Include explicit acceptance coverage with `deterministic_checks`, "
            "`requirements`, and `llm_judge_rubric`.",
            "Always provide a valid `package.json` at the starter root.",
            "Declare at least one dependency or devDependency so the engine can materialize "
            "a valid `bun.lock` with `bun install --lockfile-only` before benchmark runs.",
            "Every `required_commands` entry and every gate command must succeed on the "
            "starter before the benchmark agent edits the workspace.",
            "This is the first drafting step. Do not inspect unrelated repository paths.",
            "Do not run recursive listings like `ls -R` or broad searches.",
            "Use the objective brief and these instructions to author a minimal valid scenario.",
            "If the goal is a smoke or validation flow, prefer an `easy` scenario with a "
            "small prompt, a low-complexity starter, and only essential metrics and gates.",
            "Prefer the default metric set: functional, acceptance, "
            "verification-stability, execution-validity, resource-efficiency.",
            "Prefer a starter that uses built-in Bun capabilities and keeps dependencies "
            "minimal. If the starter would otherwise have zero packages, add one small, "
            "scenario-relevant dependency or devDependency so Bun can generate `bun.lock`.",
            "For TypeScript starters, prefer a relevant package like `typescript` over "
            "unrelated filler dependencies.",
            "Do not add failing tests or gates to the starter baseline. If you include "
            "`bun run test`, make sure the starter test suite already passes before edits.",
            "If the prompt requires a CLI output or exact runtime behavior, encode that "
            "expectation in acceptance coverage and verification commands.",
            "Write one valid JSON object to the output path and stop after the file exists.",
            "Required JSON keys:",
            DESIGN_EXAMPLE_JSON,
            "Design a typed Raidar scenario draft. Keep metrics frozen and suitable for iteration.",
        ]
        return "\n".join([*header_lines, *guidance_lines])

    def _critic_instruction(
        self,
        objective: ObjectiveState,
        draft_yaml: Path,
        design_path: Path,
        output_path: Path,
    ) -> str:
        return "\n".join(
            [
                f"Objective goal: {objective.goal}",
                f"Draft scenario yaml: {draft_yaml}",
                f"Designer plan: {design_path}",
                f"Write JSON review to: {output_path}",
                "Review only the referenced draft yaml and designer plan.",
                "Do not inspect unrelated repository paths or run recursive listings.",
                "Write one valid JSON object to the output path and stop after the file exists.",
                'Use keys: {"decision":"approve|revise|block","summary":"...","risks":["..."]}',
                "Review whether the draft scenario is suitable, typed, and measurable.",
            ]
        )

    def _planner_instruction(self, objective: ObjectiveState, output_path: Path) -> str:
        return "\n".join(
            [
                f"Objective goal: {objective.goal}",
                f"Approved scenario: {objective.scenario_ref}",
                f"Current best benchmark: {objective.best_benchmark_ref}",
                f"Loop execution mode: {objective.loop_execution_mode}",
                f"Max sibling loops this round: {objective.max_parallel_loops}",
                f"Write JSON plan to: {output_path}",
                json.dumps(
                    {
                        "loops": [
                            {
                                "loop_id": "prompt-refinement",
                                "title": "short title",
                                "hypothesis": "why this may improve outcomes",
                                "instructions": "specific bounded change direction",
                            }
                        ],
                        "notes": ["short notes"],
                    }
                ),
                "Return at most the configured number of sibling research loops.",
                "loop_id must be filesystem-safe and unique within this planner response only.",
                "The engine will namespace every loop_id by planner round before writing state.",
            ]
        )

    def _executor_instruction(
        self, objective: ObjectiveState, loop: ResearchLoopState, output_path: Path
    ) -> str:
        return "\n".join(
            [
                f"Objective goal: {objective.goal}",
                f"Loop title: {loop.title}",
                f"Loop hypothesis: {loop.hypothesis}",
                f"Candidate scenario yaml: {loop.candidate_scenario_ref}",
                f"Allowed mutation surface: {', '.join(objective.mutation_surface)}",
                f"Write execution memo JSON to: {output_path}",
                json.dumps(
                    {
                        "summary": "what changed",
                        "changed_files": ["prompt/task.md"],
                        "rationale": "why these edits align with the hypothesis",
                    }
                ),
                "Edit only the loop-local candidate scenario workspace.",
            ]
        )

    def _reviewer_instruction(
        self, objective: ObjectiveState, loop: ResearchLoopState, output_path: Path
    ) -> str:
        return "\n".join(
            [
                f"Objective goal: {objective.goal}",
                f"Baseline benchmark summary: {objective.best_benchmark_ref}",
                f"Research loop summary: {loop.latest_research_summary_ref}",
                f"Diff artifact: {loop.latest_diff_ref}",
                f"Candidate scenario yaml: {loop.candidate_scenario_ref}",
                f"Write JSON review to: {output_path}",
                json.dumps(
                    {
                        "recommended_action": "iterate|discard|promote|spawn_next|stop",
                        "summary": "review summary",
                        "strengths": ["..."],
                        "concerns": ["..."],
                    }
                ),
                "Assess the research result and recommend the next step.",
            ]
        )

    def _governor_instruction(
        self,
        objective: ObjectiveState,
        loop: ResearchLoopState,
        review_path: Path,
        output_path: Path,
    ) -> str:
        return "\n".join(
            [
                f"Objective goal: {objective.goal}",
                f"Review memo: {review_path}",
                f"Research summary: {loop.latest_research_summary_ref}",
                f"Diff artifact: {loop.latest_diff_ref}",
                f"Benchmark baseline: {objective.best_benchmark_ref}",
                f"Revision budget remaining: {objective.max_revisions - objective.revision_count}",
                f"Write governor JSON to: {output_path}",
                json.dumps(
                    {
                        "action": "iterate|discard|promote|spawn_next|stop",
                        "reasoning": "...",
                    }
                ),
                "Choose the next action within the coded constraints.",
            ]
        )
