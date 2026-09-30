"""Planner (plan D1): experiment + lock -> one Harbor trial per arm x block x problem.

Every block is one replicate slot for all arms (the pairing unit). Within a
block the arm order is counterbalanced across blocks and randomised from the
experiment seed (AB/BA for two arms; see ``arm_order``), so the order is
balanced, random and reproducible. Trials start in plan order, at most
``analysis.concurrency`` at a time.

Each trial carries content hashes of its inputs (N3): the lock, the problem,
the arm, the compiled condition (``CompiledCondition.digest``, which covers
the generated per-step ``setup.sh`` reset, so a baseline arm with no
components still has a condition digest), the agent and the protocol.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from harbor.models.trial.config import AgentConfig

from raidar.components import CompiledCondition, compile_arm
from raidar.execution.agent_config import AgentOverride, agent_digest, arm_agent_config
from raidar.execution.models import PlannedTrial, ProgressionRule, TrialHashes
from raidar.execution.progression import progression_rule
from raidar.packs.python import PackSpec
from raidar.problems.models import ProblemId
from raidar.problems.store import ProblemStore
from raidar.spec.canonical import digest_json
from raidar.spec.loader import LoadedExperiment


class PlanError(ValueError):
    """The experiment cannot be planned as locked."""


@dataclass(frozen=True)
class PlanOptions:
    """Execution-only choices; none of them changes the locked experiment.

    ``arms``/``blocks`` select a subset of the plan to run (the plan still
    lists every trial). ``max_checkpoints`` truncates every trajectory to its
    first N checkpoints (a dev/validation option; the rest stay
    ``not_attempted``). ``agent_override`` swaps in a no-LLM Harbor agent.
    ``concurrency`` replaces ``analysis.concurrency`` for this machine: it is
    not part of any trial's identity (N1 per-machine, N3), so it never
    requires a relock.
    """

    arms: tuple[str, ...] = ()
    blocks: tuple[int, ...] = ()
    max_checkpoints: int | None = None
    agent_override: AgentOverride | None = None
    concurrency: int | None = None


@dataclass(frozen=True)
class ArmBlueprint:
    arm_id: str
    compiled: CompiledCondition
    agent_config: AgentConfig


@dataclass(frozen=True)
class ProblemTarget:
    index: int
    ref: ProblemId
    digest: str
    steps: tuple[str, ...]

    def run_steps(self, max_checkpoints: int | None) -> tuple[str, ...]:
        return self.steps[:max_checkpoints] if max_checkpoints else self.steps


@dataclass(frozen=True)
class ExperimentPlan:
    loaded: LoadedExperiment
    lock_sha256: str
    options: PlanOptions
    rule: ProgressionRule
    arms: Mapping[str, ArmBlueprint]
    problems: tuple[ProblemTarget, ...]
    trials: tuple[PlannedTrial, ...]
    selected: frozenset[str] = field(default_factory=frozenset)

    @property
    def experiment_id(self) -> str:
        return self.loaded.spec.id

    @property
    def concurrency(self) -> int:
        """This machine's bound (``PlanOptions.concurrency``), else the experiment default."""
        return self.options.concurrency or self.loaded.spec.analysis.concurrency

    def selected_trials(self) -> list[PlannedTrial]:
        return [trial for trial in self.trials if trial.trial_id in self.selected]

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": "raidar-plan",
            "experiment": self.experiment_id,
            "lock_sha256": self.lock_sha256,
            "concurrency": self.concurrency,
            "progression": self.rule.model_dump(mode="json"),
            "options": {
                "arms": list(self.options.arms),
                "blocks": list(self.options.blocks),
                "max_checkpoints": self.options.max_checkpoints,
                "agent_override": self.options.agent_override,
                "concurrency": self.options.concurrency,
            },
            "arms": {
                arm_id: {
                    "condition": blueprint.compiled.condition_id,
                    "condition_digest": blueprint.compiled.digest,
                    "harbor_agent": blueprint.agent_config.name
                    or blueprint.agent_config.import_path,
                    "model": blueprint.agent_config.model_name,
                }
                for arm_id, blueprint in self.arms.items()
            },
            "trials": [
                {**trial.model_dump(mode="json"), "selected": trial.trial_id in self.selected}
                for trial in self.trials
            ],
        }


def arm_order(seed: int, block: int, problem_index: int, arms: Sequence[str]) -> list[str]:
    """The block's arm order: counterbalanced, randomised from the experiment seed.

    Blocks are grouped into cycles of ``len(arms)``. Each cycle draws one
    seeded random base order and rotates it by the block's position in the
    cycle (a cyclic Latin square), so within every complete cycle each arm
    runs in each position exactly once: AB/BA for two arms, with the pair's
    lead arm drawn at random.
    """
    count = len(arms)
    if count == 0:
        return []
    cycle, shift = divmod(block, count)
    key = hashlib.sha256(f"{seed}:{cycle}:{problem_index}".encode()).hexdigest()
    base = list(arms)
    random.Random(int(key[:16], 16)).shuffle(base)
    return base[shift:] + base[:shift]


def _lock_field(lock: Mapping[str, Any], *keys: str) -> Any:
    value: Any = lock
    for key in keys:
        if not isinstance(value, Mapping) or key not in value:
            raise PlanError(f"lock has no {'.'.join(keys)}")
        value = value[key]
    return value


def _problems(
    loaded: LoadedExperiment, lock: Mapping[str, Any], store: ProblemStore
) -> tuple[ProblemTarget, ...]:
    locked = _lock_field(lock, "problems")
    targets = []
    for index, entry in enumerate(loaded.spec.problems):
        if entry.ref is None:
            raise PlanError(
                f"problems.{index}: a local task path has no host-only oracle bundle; "
                "import the problem (`raidar problem import`) and reference it by name"
            )
        problem = ProblemId.parse(entry.ref)
        manifest = store.load_manifest(problem)
        if manifest.digest() != locked[index]["digest"]:
            raise PlanError(f"problems.{index}: {entry.ref} changed since the lock; relock")
        targets.append(
            ProblemTarget(index, problem, manifest.digest(), tuple(s.name for s in manifest.steps))
        )
    return tuple(targets)


def _blueprints(
    loaded: LoadedExperiment, pack: PackSpec, options: PlanOptions
) -> dict[str, ArmBlueprint]:
    spec = loaded.spec
    blueprints = {}
    for arm in spec.arms:
        compiled = compile_arm(spec, arm.id, base_dir=loaded.base_dir)
        config = arm_agent_config(
            spec.agent(arm.agent),
            compiled,
            pack,
            agent_timeout_sec=spec.protocol.agent_timeout_sec,
            override=options.agent_override,
        )
        blueprints[arm.id] = ArmBlueprint(arm.id, compiled, config)
    return blueprints


@dataclass(frozen=True)
class _HashInputs:
    loaded: LoadedExperiment
    lock: Mapping[str, Any]
    protocol: str
    override: AgentOverride | None

    def trial(
        self, blueprint: ArmBlueprint, problem: ProblemTarget, block: int, steps: tuple[str, ...]
    ) -> TrialHashes:
        spec = self.loaded.spec
        agent = spec.agent(next(a.agent for a in spec.arms if a.id == blueprint.arm_id))
        parts = {
            "lock": str(_lock_field(self.lock, "lock_sha256")),
            "problem": problem.digest,
            "arm": str(_lock_field(self.lock, "arms", blueprint.arm_id, "digest")),
            "condition": blueprint.compiled.digest,
            "agent": agent_digest(agent, blueprint.agent_config, self.override),
            "protocol": self.protocol,
        }
        trial = digest_json({**parts, "block": block, "steps": list(steps)})
        return TrialHashes(**parts, trial=trial)


def _selection(trials: Sequence[PlannedTrial], options: PlanOptions) -> frozenset[str]:
    return frozenset(
        trial.trial_id
        for trial in trials
        if (not options.arms or trial.arm in options.arms)
        and (not options.blocks or trial.block in options.blocks)
    )


def _check_options(loaded: LoadedExperiment, options: PlanOptions) -> None:
    spec = loaded.spec
    unknown_arms = sorted(set(options.arms) - {arm.id for arm in spec.arms})
    bad_blocks = sorted(b for b in options.blocks if not 0 <= b < spec.analysis.blocks)
    if unknown_arms or bad_blocks:
        raise PlanError(f"unknown arms {unknown_arms} or blocks {bad_blocks} in the selection")
    if options.max_checkpoints is not None and options.max_checkpoints < 1:
        raise PlanError("max_checkpoints must be >= 1")
    if options.concurrency is not None and options.concurrency < 1:
        raise PlanError("concurrency must be >= 1")


def build_plan(
    loaded: LoadedExperiment,
    lock: Mapping[str, Any],
    *,
    store: ProblemStore,
    pack: PackSpec,
    options: PlanOptions | None = None,
) -> ExperimentPlan:
    """Expand a locked experiment into its ordered trials (no side effects)."""
    options = options or PlanOptions()
    _check_options(loaded, options)
    spec = loaded.spec
    if _lock_field(lock, "experiment", "spec_sha256") != digest_json(spec.to_json()):
        raise PlanError("the lock was built from a different experiment spec; relock")
    rule = progression_rule(spec.protocol)
    protocol = digest_json(
        {
            "protocol": spec.protocol.model_dump(mode="json"),
            "rule": rule.model_dump(mode="json"),
            "max_checkpoints": options.max_checkpoints,
            "agent_override": options.agent_override,
        }
    )
    hashes = _HashInputs(loaded, lock, protocol, options.agent_override)
    problems = _problems(loaded, lock, store)
    arms = _blueprints(loaded, pack, options)
    trials: list[PlannedTrial] = []
    for block in range(spec.analysis.blocks):
        for problem in problems:
            steps = problem.run_steps(options.max_checkpoints)
            order = arm_order(spec.analysis.seed, block, problem.index, list(arms))
            for position, arm_id in enumerate(order):
                trials.append(
                    PlannedTrial(
                        trial_id=f"b{block:02d}-p{problem.index}-{arm_id}",
                        block=block,
                        order=position,
                        sequence=len(trials),
                        arm=arm_id,
                        problem=str(problem.ref),
                        problem_index=problem.index,
                        steps=steps,
                        hashes=hashes.trial(arms[arm_id], problem, block, steps),
                    )
                )
    return ExperimentPlan(
        loaded=loaded,
        lock_sha256=str(_lock_field(lock, "lock_sha256")),
        options=options,
        rule=rule,
        arms=arms,
        problems=problems,
        trials=tuple(trials),
        selected=_selection(trials, options),
    )
