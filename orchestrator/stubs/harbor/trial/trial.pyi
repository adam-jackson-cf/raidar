import abc
import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, override
from uuid import UUID

from _typeshed import Incomplete
from harbor.agents.base import BaseAgent as BaseAgent
from harbor.agents.factory import AgentFactory as AgentFactory
from harbor.bridges.base import BaseBridge as BaseBridge
from harbor.constants import MAIN_SERVICE_NAME as MAIN_SERVICE_NAME
from harbor.environments.base import (
    BaseEnvironment as BaseEnvironment,
    OutputStream as OutputStream,
)
from harbor.environments.factory import EnvironmentFactory as EnvironmentFactory
from harbor.models.agent.context import AgentContext as AgentContext
from harbor.models.agent.name import AgentName as AgentName
from harbor.models.job.lock import TrialLock as TrialLock, build_trial_lock as build_trial_lock
from harbor.models.task.artifacts import (
    sidecar_services as sidecar_services,
    validate_artifact_entries as validate_artifact_entries,
)
from harbor.models.task.config import (
    EnvironmentConfig as EnvironmentConfig,
    MultiStepRewardStrategy as MultiStepRewardStrategy,
    NetworkPolicy as NetworkPolicy,
    StepConfig as StepConfig,
    TaskOS as TaskOS,
    VerifierCollectConfig as VerifierCollectConfig,
    VerifierEnvironmentMode as VerifierEnvironmentMode,
)
from harbor.models.task.paths import TaskPaths as TaskPaths
from harbor.models.task.task import Task as Task
from harbor.models.task.verifier_mode import (
    resolve_effective_verifier_env_config as resolve_effective_verifier_env_config,
    resolve_step_verifier_mode as resolve_step_verifier_mode,
    resolve_task_verifier_mode as resolve_task_verifier_mode,
)
from harbor.models.trajectories import Trajectory as Trajectory
from harbor.models.trial.config import (
    AgentConfig as AgentConfig,
    ArtifactConfig as ArtifactConfig,
    ServiceVolumeConfig as ServiceVolumeConfig,
    TrialConfig as TrialConfig,
)
from harbor.models.trial.paths import EnvironmentPaths as EnvironmentPaths, TrialPaths as TrialPaths
from harbor.models.trial.result import (
    ExceptionInfo as ExceptionInfo,
    StepResult as StepResult,
    TimingInfo as TimingInfo,
    TrialResult as TrialResult,
)
from harbor.models.verifier.result import VerifierResult as VerifierResult
from harbor.skills import (
    ResolvedSkill as ResolvedSkill,
    resolve_skill_sources as resolve_skill_sources,
    resolve_skills as resolve_skills,
)
from harbor.tasks.client import TaskClient as TaskClient, TaskDownloadResult as TaskDownloadResult
from harbor.trial.artifact_handler import ArtifactHandler as ArtifactHandler
from harbor.trial.errors import (
    AgentSetupTimeoutError as AgentSetupTimeoutError,
    AgentTimeoutError as AgentTimeoutError,
    EnvironmentStartTimeoutError as EnvironmentStartTimeoutError,
)
from harbor.trial.hooks import (
    LogCallback as LogCallback,
    LogEntry as LogEntry,
    LogPhase as LogPhase,
    TrialEvent as TrialEvent,
    TrialHookEvent as TrialHookEvent,
)
from harbor.trial.network_policy import (
    TrialNetworkPlan as TrialNetworkPlan,
    resolve_trial_network_plan as resolve_trial_network_plan,
)
from harbor.trial.simulated_user import (
    load_user_persona as load_user_persona,
    load_user_prompt_template as load_user_prompt_template,
    render_user_prompt as render_user_prompt,
    validate_user_agent_version_pin as validate_user_agent_version_pin,
)
from harbor.utils.env import (
    is_sensitive_env_key as is_sensitive_env_key,
    resolve_env_vars as resolve_env_vars,
)
from harbor.utils.scripts import quote_shell_arg as quote_shell_arg
from harbor.utils.trajectory_utils import (
    load_model_usage_from_trajectory as load_model_usage_from_trajectory,
)
from harbor.verifier.factory import VerifierFactory as VerifierFactory

TrialHookCallback = Callable[[TrialHookEvent], Awaitable[None]]
_MAX_VERIFIER_ENV_SESSION_ID_LEN: int
_UNSET_BRIDGE_USER: Incomplete

class Trial(ABC, metaclass=abc.ABCMeta):
    _AGENT_SETUP_TIMEOUT_SEC: int = ...
    _load_trajectory: Path | None = ...
    _task_trajectory_error: Exception | None = ...
    config: Incomplete
    job_id: Incomplete
    _id: Incomplete
    task: Incomplete
    _task_download_result: Incomplete
    paths: Incomplete
    _trial_lock: Incomplete
    agent_env_paths: Incomplete
    _injected_skills: Incomplete
    _effective_skills_dir: Incomplete
    _hooks: dict[TrialEvent, list[TrialHookCallback]]
    _are_agent_logs_downloaded: bool
    _is_agent_environment_stopped: bool
    bridge: BaseBridge | None
    _bridge_setup_started: bool
    _bridge_ready: bool
    _bridge_closed: bool
    _bridge_cleanup_task: asyncio.Task[None] | None
    _result: TrialResult | None
    _log_handler: logging.Handler | None
    _log_callbacks: list[LogCallback]
    def __init__(
        self,
        config: TrialConfig,
        *,
        _task: Task | None = None,
        _task_download_result: TaskDownloadResult,
    ) -> None: ...
    @property
    def id(self) -> UUID: ...
    @property
    def result(self) -> TrialResult: ...
    @staticmethod
    def _now() -> datetime: ...
    def _network_plan(
        self, step_cfg: StepConfig | None = None, *, env_config: EnvironmentConfig | None = None
    ) -> TrialNetworkPlan: ...
    def _validate_network_policy_modes(self) -> None: ...
    def _validate_network_plan(self, plan: TrialNetworkPlan, *, label: str) -> None: ...
    def _validate_dynamic_phase_switch(
        self,
        environment: BaseEnvironment,
        *,
        phase: NetworkPolicy,
        phase_label: str,
        environment_label: str,
    ) -> None: ...
    def _validate_separate_verifier_env_policies(
        self, environment: BaseEnvironment, *, plan: TrialNetworkPlan
    ) -> None: ...
    def _phase_network_policy(
        self,
        environment: BaseEnvironment,
        *,
        baseline_policy: NetworkPolicy,
        phase_policy: NetworkPolicy,
    ) -> contextlib.AbstractAsyncContextManager[None]: ...
    @classmethod
    async def create(cls, config: TrialConfig) -> Trial: ...
    async def _create_bridge(self) -> None: ...
    @staticmethod
    async def _load_task(config: TrialConfig) -> tuple[Task, TaskDownloadResult]: ...
    def add_hook(self, event: TrialEvent, hook: TrialHookCallback) -> None: ...
    def add_log_callback(self, callback: LogCallback) -> None: ...
    def _log_context(
        self, phase: LogPhase, environment: BaseEnvironment, step_name: str | None = None
    ) -> contextlib.AbstractContextManager[None]: ...
    async def _emit(self, event: TrialEvent) -> None: ...
    async def run(self) -> TrialResult: ...
    @abstractmethod
    async def _run(self) -> None: ...
    @abstractmethod
    async def _recover_outputs(self) -> None: ...
    async def _prepare(self) -> None: ...
    async def _finalize(self) -> None: ...
    def _record_exception(self, exc: BaseException) -> None: ...
    def _resolve_timeout_sec(
        self, *, base_sec: float, max_sec: float | None = None, multiplier: float | None
    ) -> float: ...
    async def _run_agent_phase(
        self,
        *,
        target: TrialResult | StepResult,
        instruction: str,
        timeout_sec: float | None,
        user: str | int | None,
        step_cfg: StepConfig | None = None,
        resume: bool = False,
        load: bool = False,
    ) -> None: ...
    async def _download_agent_logs(self) -> None: ...
    async def _download_role_logs(
        self, *, agent_config: AgentConfig, source_dir: PurePosixPath, target_dir: Path
    ) -> None: ...
    async def _upload_agent_logs(self) -> None: ...
    async def _upload_role_logs(self, *, source_dir: Path, target_dir: PurePosixPath) -> None: ...
    async def _run_step_verifier(
        self,
        step: StepConfig,
        step_result: StepResult,
        *,
        artifacts_dir: Path,
        mode: VerifierEnvironmentMode,
    ) -> None: ...
    def _select_multi_step_reward(self) -> VerifierResult | None: ...
    def _aggregate_step_rewards(self) -> VerifierResult | None: ...
    async def _reset_shared_step_verifier_dirs(self) -> None: ...
    def _step_verifier_timeout_sec(self, step: StepConfig) -> float | None: ...
    def _step_verifier_user(self, step: StepConfig) -> str | int | None: ...
    async def _run_shared_verifier(
        self,
        *,
        timeout_sec: float | None,
        user: str | int | None,
        env: dict[str, str] | None = None,
        step_name: str | None = None,
        step_cfg: StepConfig | None = None,
    ) -> VerifierResult: ...
    async def _run_separate_verifier(
        self,
        *,
        key: str,
        timeout_sec: float | None,
        artifacts_dir: Path,
        artifacts: Sequence[str | ArtifactConfig] | None = None,
        step_cfg: StepConfig | None = None,
        user: str | int | None,
        env: dict[str, str] | None = None,
    ) -> VerifierResult: ...
    def _separate_verifier_env(
        self,
        env_config: EnvironmentConfig,
        *,
        key: str,
        plan: TrialNetworkPlan,
        step_cfg: StepConfig | None = None,
    ) -> contextlib.AbstractAsyncContextManager[BaseEnvironment]: ...
    def _verifier_env_mounts(self, env_config: EnvironmentConfig) -> list[ServiceVolumeConfig]: ...
    def _verifier_env_build_context(self, step_cfg: StepConfig | None) -> Path: ...
    def _separate_verifier_session_id(self, key: str) -> str: ...
    def _populate_agent_context(self, agent_result: AgentContext | None) -> None: ...
    async def _sync_agent_output(self, target: TrialResult | StepResult) -> None: ...
    def _init_result(self) -> None: ...
    def _write_trial_lock(self) -> TrialLock: ...
    def _regrade_source_dir(self) -> Path | None: ...
    logger: Incomplete
    def _init_logger(self) -> None: ...
    def _close_logger_handler(self) -> None: ...
    def _scrub_jobs_dir(self) -> None: ...
    agent: Incomplete
    def _init_agent(self) -> None: ...
    def _resolve_load_trajectory(self) -> None: ...
    @property
    def _load_trajectory_from_task(self) -> bool: ...
    def _task_trajectory_path(self) -> Path | None: ...
    @staticmethod
    def _validate_task_trajectory_document(path: Path) -> None: ...
    def _validate_load_trajectory_support(self) -> None: ...
    user_agent: BaseAgent | None
    def _init_user_agent(self) -> None: ...
    agent_environment: Incomplete
    def _init_agent_environment(self) -> None: ...
    @property
    def _main_artifacts_mount_dir(self) -> Path: ...
    def _prepare_artifact_mount_dirs(self) -> None: ...
    def _chmod_artifact_mount_chain(self) -> None: ...
    _artifact_handler: Incomplete
    def _init_artifact_handler(self) -> None: ...
    def _validate_artifact_configuration(self) -> None: ...
    def _referenced_sidecar_services(self) -> set[str]: ...
    def _all_collect_hooks(self) -> list[VerifierCollectConfig]: ...
    def _collect_hooks_for(self, step_cfg: StepConfig | None) -> list[VerifierCollectConfig]: ...
    async def _run_collect_hooks(self, hooks: Sequence[VerifierCollectConfig]) -> None: ...
    async def _collect_artifacts_phased(
        self,
        *,
        artifacts_dir: Path,
        step_cfg: StepConfig | None = None,
        step_artifacts: Sequence[str | ArtifactConfig] | None = None,
        stop_main_before_sidecars: bool = False,
    ) -> None: ...
    _agent_timeout_sec: Incomplete
    _verifier_timeout_sec: Incomplete
    _agent_setup_timeout_sec: Incomplete
    _environment_build_timeout_sec: Incomplete
    def _init_timeouts(self) -> None: ...
    def _compute_agent_timeout_sec(self) -> float | None: ...
    def _compute_verifier_timeout_sec(self) -> float: ...
    def _compute_agent_setup_timeout_sec(self) -> float: ...
    def _compute_environment_build_timeout_sec(self) -> float: ...
    def _resolve_injected_skills(self) -> list[ResolvedSkill]: ...
    @staticmethod
    def _resolve_agent_skills(config: TrialConfig) -> None: ...
    def _resolve_effective_skills_dir(self) -> str | None: ...
    async def _upload_injected_skills(self) -> None: ...
    async def _setup_agent_environment(self) -> None: ...
    async def _start_agent_environment(self) -> None: ...
    async def _setup_agent(self) -> None: ...
    async def _setup_user_agent(self) -> None: ...
    @property
    def _bridge_trajectory_path(self) -> Path: ...
    def _bridge_exec_env(self) -> dict[str, str]: ...
    async def _setup_bridge(self) -> None: ...
    async def _cleanup_bridge(self, user: str | int | None) -> None: ...
    async def _close_bridge(self, user: Any = ...) -> None: ...
    async def _stop_agent_environment(self) -> None: ...
    @property
    def _agent_env_mounts(self) -> list[ServiceVolumeConfig]: ...
    @override
    def __repr__(self) -> str: ...
