from dataclasses import dataclass
from typing import Literal

from harbor.models.task.config import (
    EnvironmentConfig as EnvironmentConfig,
    NetworkMode as NetworkMode,
    NetworkPolicy as NetworkPolicy,
    StepConfig as StepConfig,
    TaskConfig as TaskConfig,
    VerifierEnvironmentMode as VerifierEnvironmentMode,
    normalize_allowed_hosts as normalize_allowed_hosts,
)
from harbor.models.task.verifier_mode import (
    resolve_effective_verifier_env_config as resolve_effective_verifier_env_config,
)
from harbor.models.trial.config import (
    AgentConfig as TrialAgentConfig,
    EnvironmentConfig as TrialEnvironmentConfig,
)

def merge_extra_allowlists(
    policy: NetworkPolicy, extra_allowed_hosts: list[str]
) -> NetworkPolicy: ...
def _explicit_phase_policy(
    task_cfg: TaskConfig, step_cfg: StepConfig | None, role: Literal["agent", "verifier"]
) -> NetworkPolicy | None: ...
def _verifier_inherits_task_environment(
    task_cfg: TaskConfig, step_cfg: StepConfig | None
) -> bool: ...
def _merge_environment_host_overrides(
    baseline: NetworkPolicy, trial_env_cfg: TrialEnvironmentConfig
) -> NetworkPolicy: ...
def resolve_agent_env_baseline(
    task_cfg: TaskConfig, trial_env_cfg: TrialEnvironmentConfig
) -> NetworkPolicy: ...
def resolve_verifier_env_baseline(
    task_cfg: TaskConfig,
    trial_env_cfg: TrialEnvironmentConfig,
    step_cfg: StepConfig | None,
    *,
    env_config: EnvironmentConfig,
) -> NetworkPolicy: ...
def resolve_agent_phase_policy(
    task_cfg: TaskConfig,
    trial_agent_cfg: TrialAgentConfig,
    agent_env_baseline: NetworkPolicy,
    step_cfg: StepConfig | None = None,
) -> NetworkPolicy: ...
def resolve_verifier_phase_policy(
    task_cfg: TaskConfig, step_cfg: StepConfig | None = None, *, baseline: NetworkPolicy
) -> NetworkPolicy: ...
@dataclass(frozen=True)
class TrialNetworkPlan:
    agent_env_baseline: NetworkPolicy
    agent_phase: NetworkPolicy
    verifier_env_baseline: NetworkPolicy | None
    verifier_phase: NetworkPolicy
    @property
    def verifier_phase_baseline(self) -> NetworkPolicy: ...

def resolve_trial_network_plan(
    task_cfg: TaskConfig,
    trial_agent_cfg: TrialAgentConfig,
    trial_env_cfg: TrialEnvironmentConfig,
    step_cfg: StepConfig | None,
    *,
    verifier_mode: VerifierEnvironmentMode,
    env_config: EnvironmentConfig | None = None,
) -> TrialNetworkPlan: ...
