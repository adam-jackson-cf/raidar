"""Cross-check loadable lab agents against runtime-pack tool pins."""

from __future__ import annotations

from pathlib import Path

from raidar_contract.agent import AgentDefinitionError, agent_names
from raidar_contract.workflow import PackageWorkflowError

from raidar.subject.agent.definition import harness_of, load_agent
from raidar.subject.agent.schema import AgentDefinition
from raidar.subject.environment import available_runtimes, load_runtime_pack


def agent_pin_issues(root: Path) -> list[str]:
    """Compare loadable agent tool pins with each supported runtime pack's baked harness."""
    agents = _loadable_agents(root)
    issues = []
    for runtime in available_runtimes():
        pack = load_runtime_pack(runtime)
        for agent in agents:
            harness = harness_of(agent)
            pin = pack.image.harnesses.get(harness.value)
            if pin is None:
                continue
            baked = (pin.npm_package, pin.executable, pin.version)
            pinned = (agent.tool.package, agent.tool.binary, agent.tool.version)
            if baked != pinned:
                issues.append(
                    f"agents/{agent.name}: tool {pinned} differs from pack {pack.id} "
                    f"image.harnesses.{harness.value} {baked}; the agent pin is the source"
                )
    return issues


def _loadable_agents(root: Path) -> list[AgentDefinition]:
    """The agents that load (a broken one is already reported by the registration check)."""
    agents = []
    for name in agent_names(root):
        try:
            agents.append(load_agent(root, name))
        except (AgentDefinitionError, PackageWorkflowError):
            continue
    return agents
