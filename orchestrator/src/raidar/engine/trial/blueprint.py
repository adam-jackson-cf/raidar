"""The resolved arm and Harbor agent config of each arm of an experiment (plan D2)."""

from __future__ import annotations

from dataclasses import dataclass

from harbor.models.trial.config import AgentConfig

from raidar.design.experiment.arm import ResolvedArm, resolve_arm
from raidar.design.experiment.loader import LoadedExperiment
from raidar.engine.trial.agent_config import AgentOverride, arm_agent_config
from raidar.subject.environment.python.pack_definition import PackSpec


@dataclass(frozen=True)
class ArmBlueprint:
    arm_id: str
    arm: ResolvedArm
    agent_config: AgentConfig


def arm_blueprints(
    loaded: LoadedExperiment, pack: PackSpec, *, agent_override: AgentOverride | None
) -> dict[str, ArmBlueprint]:
    spec = loaded.spec
    blueprints = {}
    for item in spec.arms:
        arm = resolve_arm(spec, item.id)
        config = arm_agent_config(
            arm,
            pack,
            agent_timeout_sec=spec.protocol.agent_timeout_sec,
            override=agent_override,
        )
        blueprints[item.id] = ArmBlueprint(item.id, arm, config)
    return blueprints
