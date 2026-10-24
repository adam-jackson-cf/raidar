"""The compiled condition and Harbor agent config of each arm of an experiment (plan D2)."""

from __future__ import annotations

from dataclasses import dataclass

from harbor.models.trial.config import AgentConfig

from raidar.design.experiment.arm import compile_arm
from raidar.design.experiment.loader import LoadedExperiment
from raidar.engine.trial.agent_config import AgentOverride, arm_agent_config
from raidar.subject.environment.python.pack_definition import PackSpec
from raidar.subject.treatment import CompiledCondition


@dataclass(frozen=True)
class ArmBlueprint:
    arm_id: str
    compiled: CompiledCondition
    agent_config: AgentConfig


def arm_blueprints(
    loaded: LoadedExperiment, pack: PackSpec, *, agent_override: AgentOverride | None
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
            override=agent_override,
        )
        blueprints[arm.id] = ArmBlueprint(arm.id, compiled, config)
    return blueprints
