"""Planner (plan D1): experiment + lock -> one Harbor trial per arm x attempt x problem.

Every attempt is one replicate slot for all arms (the pairing unit). Within an
attempt the arm order is counterbalanced across attempts and randomised from the
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

from raidar.contract.canonical import digest_json
from raidar.contract.trial import PlannedTrial, ProgressionRule, TrialHashes
from raidar.design.experiment.loader import LoadedExperiment
from raidar.design.problem.models import ProblemId
from raidar.design.problem.store import ProblemStore, ProblemStoreError
from raidar.engine.trial.agent_config import AgentOverride, agent_digest
from raidar.engine.trial.blueprint import ArmBlueprint, arm_blueprints
from raidar.engine.trial.progression import progression_rule
from raidar.subject.environment.python.pack_definition import PackSpec


class PlanError(ValueError):
    """The experiment cannot be planned as locked."""


@dataclass(frozen=True)
class PlanOptions:
    """Execution-only choices; none of them changes the locked experiment.

    ``arms``/``attempts`` select a subset of the plan to run (the plan still
    lists every trial). ``max_checkpoints`` truncates every trial to its
    first N checkpoints (a dev/validation option; the rest stay
    ``not_attempted``). ``agent_override`` swaps in a no-LLM Harbor agent.
    ``concurrency`` replaces ``analysis.concurrency`` for this machine: it is
    not part of any trial's identity (N1 per-machine, N3), so it never
    requires a relock.
    """

    arms: tuple[str, ...] = ()
    attempts: tuple[int, ...] = ()
    max_checkpoints: int | None = None
    agent_override: AgentOverride | None = None
    concurrency: int | None = None


@dataclass(frozen=True)
class ProblemTarget:
    index: int
    ref: ProblemId
    digest: str
    steps: tuple[str, ...]
    errata: Mapping[str, Any] | None = None
    """The errata the lock pins (``sha256``, ``exclude_from_solved``); the verifier records
    its verdicts."""

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
    lock: Mapping[str, Any] = field(default_factory=dict)
    """The lock document the plan was built from; a job keeps a copy of it."""

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
            "schema_version": 2,
            "experiment": self.experiment_id,
            "lock_sha256": self.lock_sha256,
            "concurrency": self.concurrency,
            "progression": self.rule.model_dump(mode="json"),
            "options": {
                "arms": list(self.options.arms),
                "attempts": list(self.options.attempts),
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


def arm_order(seed: int, attempt: int, problem_index: int, arms: Sequence[str]) -> list[str]:
    """The attempt's arm order: counterbalanced, randomised from the experiment seed.

    Attempts are grouped into cycles of ``len(arms)``. Each cycle draws one
    seeded random base order and rotates it by the attempt's position in the
    cycle (a cyclic Latin square), so within every complete cycle each arm
    runs in each position exactly once: AB/BA for two arms, with the pair's
    lead arm drawn at random.
    """
    count = len(arms)
    if count == 0:
        return []
    cycle, shift = divmod(attempt, count)
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
    locked = _lock_field(lock, "execution", "problems")
    targets = []
    for index, entry in enumerate(loaded.spec.problems):
        if entry.ref is None:
            raise PlanError(
                f"problems.{index}: a local task path has no host-only verifier bundle; "
                "import the problem (`raidar problem import`) and reference it by name"
            )
        problem = ProblemId.parse(entry.ref)
        manifest = store.load_manifest(problem)
        if manifest.digest() != locked[index]["digest"]:
            raise PlanError(f"problems.{index}: {entry.ref} changed since the lock; relock")
        references = _lock_field(lock, "execution", "references").get("problem", {})
        pinned = ((references.get(entry.ref) or {}).get("metadata") or {}).get("errata")
        try:
            errata = store.pinned_errata(problem, manifest, pinned)
        except ProblemStoreError as exc:
            raise PlanError(f"problems.{index}: {exc}") from exc
        targets.append(
            ProblemTarget(
                index, problem, manifest.digest(), tuple(s.name for s in manifest.steps), errata
            )
        )
    return tuple(targets)


@dataclass(frozen=True)
class _HashInputs:
    loaded: LoadedExperiment
    lock: Mapping[str, Any]
    protocol: str
    override: AgentOverride | None

    def trial(
        self, blueprint: ArmBlueprint, problem: ProblemTarget, attempt: int, steps: tuple[str, ...]
    ) -> TrialHashes:
        spec = self.loaded.spec
        agent = spec.agent(next(a.agent for a in spec.arms if a.id == blueprint.arm_id))
        parts = {
            "lock": str(_lock_field(self.lock, "lock_sha256")),
            "problem": problem.digest,
            "arm": str(_lock_field(self.lock, "execution", "arms", blueprint.arm_id, "digest")),
            "condition": blueprint.compiled.digest,
            "agent": agent_digest(agent, blueprint.agent_config, self.override),
            "protocol": self.protocol,
        }
        trial = digest_json({**parts, "attempt": attempt, "steps": list(steps)})
        return TrialHashes(**parts, trial=trial)


def _selection(trials: Sequence[PlannedTrial], options: PlanOptions) -> frozenset[str]:
    return frozenset(
        trial.trial_id
        for trial in trials
        if (not options.arms or trial.arm in options.arms)
        and (not options.attempts or trial.attempt in options.attempts)
    )


def _check_options(loaded: LoadedExperiment, options: PlanOptions) -> None:
    spec = loaded.spec
    unknown_arms = sorted(set(options.arms) - {arm.id for arm in spec.arms})
    bad_attempts = sorted(b for b in options.attempts if not 0 <= b < spec.analysis.attempts)
    if unknown_arms or bad_attempts:
        raise PlanError(f"unknown arms {unknown_arms} or attempts {bad_attempts} in the selection")
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
    arms = arm_blueprints(loaded, pack, agent_override=options.agent_override)
    trials: list[PlannedTrial] = []
    for attempt in range(spec.analysis.attempts):
        for problem in problems:
            steps = problem.run_steps(options.max_checkpoints)
            order = arm_order(spec.analysis.seed, attempt, problem.index, list(arms))
            for position, arm_id in enumerate(order):
                trials.append(
                    PlannedTrial(
                        trial_id=f"b{attempt:02d}-p{problem.index}-{arm_id}",
                        attempt=attempt,
                        order=position,
                        sequence=len(trials),
                        arm=arm_id,
                        problem=str(problem.ref),
                        problem_index=problem.index,
                        steps=steps,
                        hashes=hashes.trial(arms[arm_id], problem, attempt, steps),
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
        lock=lock,
    )
