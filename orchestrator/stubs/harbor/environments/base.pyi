import abc
import contextlib
import contextvars
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Generator, Sequence
from functools import cached_property as cached_property
from pathlib import Path, PurePath
from typing import Literal
from uuid import UUID

from _typeshed import Incomplete
from harbor.constants import MAIN_SERVICE_NAME as MAIN_SERVICE_NAME
from harbor.environments.capabilities import (
    EnvironmentCapabilities as EnvironmentCapabilities,
    EnvironmentResourceCapabilities as EnvironmentResourceCapabilities,
)
from harbor.environments.definition import (
    environment_content_hash as environment_content_hash,
    should_upload_environment_dir as should_upload_environment_dir,
)
from harbor.environments.resource_policies import (
    validate_resource_capabilities as validate_resource_capabilities,
    validate_resource_values as validate_resource_values,
)
from harbor.models.task.config import (
    EnvironmentConfig as EnvironmentConfig,
    HealthcheckConfig as HealthcheckConfig,
    NetworkAllowlistEntryType as NetworkAllowlistEntryType,
    NetworkMode as NetworkMode,
    NetworkPolicy as NetworkPolicy,
    TaskOS as TaskOS,
    TpuSpec as TpuSpec,
    classify_network_allowlist_entry as classify_network_allowlist_entry,
)
from harbor.models.trial.config import (
    ResourceMode as ResourceMode,
    ServiceVolumeConfig as ServiceVolumeConfig,
)
from harbor.models.trial.paths import TrialPaths as TrialPaths
from harbor.utils.env import resolve_env_vars as resolve_env_vars
from harbor.utils.path_filter import filter_paths_by_patterns as filter_paths_by_patterns
from harbor.utils.scripts import quote_shell_arg as quote_shell_arg
from pydantic import BaseModel

EnvironmentPath = str | PurePath
_TRANSFER_TAR_TEMPLATE: str
_TRANSFER_LIST_TEMPLATE: str
_ENV_TRANSFER_TAR_DIR: Incomplete
_TRANSFER_PACK_TIMEOUT_SEC: int
OutputStream: Incomplete
OutputCallback = Callable[[str, OutputStream], Awaitable[None]]
ExecEnvOverlays = tuple[dict[str, str], ...]

class HealthcheckError(RuntimeError): ...
class ServiceOperationsUnsupportedError(RuntimeError): ...
class SandboxBuildFailedError(Exception): ...

class ExecResult(BaseModel):
    stdout: str | None = ...
    stderr: str | None = ...
    return_code: int

class BaseEnvironment(ABC, metaclass=abc.ABCMeta):
    environment_dir: Path
    environment_name: str
    session_id: str
    context_id: UUID | None = ...
    trial_paths: TrialPaths
    task_env_config: EnvironmentConfig
    extra_docker_compose_paths: list[Path]
    logger: logging.Logger
    default_user: str | int | None
    _override_cpus: Incomplete
    _override_memory_mb: Incomplete
    _override_storage_mb: Incomplete
    _override_gpus: Incomplete
    _override_tpu: Incomplete
    _cpu_resource_mode: Incomplete
    _memory_resource_mode: Incomplete
    _persistent_env: dict[str, str]
    _mounts: list[ServiceVolumeConfig]
    _network_policy: Incomplete
    _phase_network_policies: list[NetworkPolicy]
    _output_callbacks: contextvars.ContextVar[tuple[OutputCallback, ...]]
    _exec_env_overlays: contextvars.ContextVar[ExecEnvOverlays]
    def __init__(
        self,
        environment_dir: Path,
        environment_name: str,
        session_id: str,
        trial_paths: TrialPaths,
        task_env_config: EnvironmentConfig,
        logger: logging.Logger | None = None,
        override_cpus: int | None = None,
        override_memory_mb: int | None = None,
        override_storage_mb: int | None = None,
        override_gpus: int | None = None,
        override_tpu: TpuSpec | None = None,
        cpu_enforcement_policy: ResourceMode = ...,
        memory_enforcement_policy: ResourceMode = ...,
        persistent_env: dict[str, str] | None = None,
        mounts: list[ServiceVolumeConfig] | None = None,
        network_policy: NetworkPolicy | None = None,
        phase_network_policies: Sequence[NetworkPolicy] | None = None,
        extra_docker_compose: Sequence[Path | str] | None = None,
        *args: object,
        **kwargs: object,
    ) -> None: ...
    @cached_property
    def environment_id(self) -> str: ...
    @staticmethod
    def _normalize_extra_docker_compose_paths(paths: Sequence[Path | str] | None) -> list[Path]: ...
    @property
    def network_policy(self) -> NetworkPolicy: ...
    @property
    def _network_disabled(self) -> bool: ...
    @property
    def _network_is_public(self) -> bool: ...
    @property
    def _network_is_allowlist(self) -> bool: ...
    @property
    def _uses_compose(self) -> bool: ...
    def _validate_extra_docker_compose_support(self) -> None: ...
    def _maybe_resolve_task_env(self) -> None: ...
    def _startup_env(self) -> dict[str, str]: ...
    def _maybe_override_task_env_config(self) -> None: ...
    def _resource_mode(self, resource: Literal["cpu", "memory"]) -> ResourceMode: ...
    def _resource_value(self, resource: Literal["cpu", "memory"]) -> int | None: ...
    def _resource_request_value(
        self, resource: Literal["cpu", "memory"], *, auto_mode: ResourceMode
    ) -> int | None: ...
    def _resource_limit_value(
        self, resource: Literal["cpu", "memory"], *, auto_mode: ResourceMode
    ) -> int | None: ...
    def _resource_policy_value(
        self, resource: Literal["cpu", "memory"], *, target: ResourceMode, auto_mode: ResourceMode
    ) -> int | None: ...
    @property
    def _effective_cpus(self) -> int | None: ...
    @property
    def _effective_memory_mb(self) -> int | None: ...
    @property
    def _effective_storage_mb(self) -> int | None: ...
    @property
    def _effective_gpus(self) -> int: ...
    def _validate_resource_mode_support(self) -> None: ...
    def _resolve_user(self, user: str | int | None) -> str | int | None: ...
    @contextlib.contextmanager
    def with_default_user(self, user: str | int | None) -> Generator[None]: ...
    def _merge_env(self, env: dict[str, str] | None) -> dict[str, str] | None: ...
    @contextlib.contextmanager
    def scoped_exec_env(self, env: dict[str, str]) -> Generator[None]: ...
    @contextlib.contextmanager
    def scoped_output_callback(self, callback: OutputCallback | None) -> Generator[None]: ...
    def _output_callback(self) -> OutputCallback | None: ...
    def _reset_dirs_command(
        self,
        *,
        remove_dirs: Sequence[EnvironmentPath],
        create_dirs: Sequence[EnvironmentPath],
        chmod_dirs: Sequence[EnvironmentPath] | None = None,
    ) -> str: ...
    def _ensure_dirs_command(
        self, dirs: Sequence[EnvironmentPath], *, chmod: bool = True
    ) -> str: ...
    def _empty_dirs_command(
        self, dirs: Sequence[EnvironmentPath], *, chmod: bool = True
    ) -> str: ...
    def _reset_dirs_user(self) -> str | None: ...
    async def reset_dirs(
        self,
        *,
        remove_dirs: Sequence[EnvironmentPath],
        create_dirs: Sequence[EnvironmentPath],
        chmod_dirs: Sequence[EnvironmentPath] | None = None,
    ) -> ExecResult: ...
    async def ensure_dirs(
        self, dirs: Sequence[EnvironmentPath], *, chmod: bool = True
    ) -> ExecResult | None: ...
    async def empty_dirs(
        self, dirs: Sequence[EnvironmentPath], *, chmod: bool = True
    ) -> ExecResult | None: ...
    def _mount_targets(self, *, writable_only: bool = False) -> list[str]: ...
    @staticmethod
    @abstractmethod
    def type() -> str: ...
    @property
    def os(self) -> TaskOS: ...
    @property
    def task_os(self) -> TaskOS: ...
    _LEGACY_CAPABILITY_ATTRS: dict[str, str] = ...
    def __init_subclass__(cls, **kwargs: object) -> None: ...
    @property
    def capabilities(self) -> EnvironmentCapabilities: ...
    @classmethod
    def resource_capabilities(cls) -> EnvironmentResourceCapabilities | None: ...
    @abstractmethod
    def _validate_definition(self) -> None: ...
    def _validate_gpu_support(self) -> None: ...
    def _validate_tpu_support(self) -> None: ...
    def validate_network_policy_support(
        self, network_policy: NetworkPolicy | None = None
    ) -> None: ...
    def _validate_network_policy_support(self) -> None: ...
    async def set_network_policy(self, network_policy: NetworkPolicy) -> None: ...
    async def _apply_network_policy(self, network_policy: NetworkPolicy) -> None: ...
    def _validate_windows_support(self) -> None: ...
    @classmethod
    def preflight(cls) -> None: ...
    async def _upload_environment_dir_after_start(self) -> None: ...
    @abstractmethod
    async def start(self, force_build: bool) -> None: ...
    @abstractmethod
    async def stop(self, delete: bool) -> None: ...
    async def prepare_logs_for_host(self) -> None: ...
    @abstractmethod
    async def upload_file(self, source_path: Path | str, target_path: str) -> None: ...
    @abstractmethod
    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None: ...
    @abstractmethod
    async def download_file(self, source_path: str, target_path: Path | str) -> None: ...
    @abstractmethod
    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None: ...
    async def download_dir_with_exclusions(
        self, *, source_dir: str, target_dir: Path | str, exclude: list[str]
    ) -> None: ...
    async def _download_dir_with_exclusions_impl(
        self, *, source_dir: str, target_dir: Path | str, exclude: list[str], service: str | None
    ) -> None: ...
    async def download_dir_filtered(
        self,
        *,
        source_dir: str,
        target_dir: Path | str,
        include: Sequence[str] | None = None,
        exclude: Sequence[str] | None = None,
        protect: Sequence[str] | None = None,
    ) -> None: ...
    @abstractmethod
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult: ...
    async def is_dir(self, path: str, user: str | int | None = None) -> bool: ...
    async def is_file(self, path: str, user: str | int | None = None) -> bool: ...
    @staticmethod
    def is_main_service(service: str | None) -> bool: ...
    def _service_unsupported_message(self, service: str) -> str: ...
    async def service_exec(
        self,
        command: str,
        *,
        service: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult: ...
    async def service_download_file(
        self, source_path: str, target_path: Path | str, *, service: str | None = None
    ) -> None: ...
    async def service_download_dir(
        self, source_dir: str, target_dir: Path | str, *, service: str | None = None
    ) -> None: ...
    async def service_download_dir_with_exclusions(
        self,
        *,
        source_dir: str,
        target_dir: Path | str,
        exclude: list[str],
        service: str | None = None,
    ) -> None: ...
    async def service_is_dir(
        self, path: str, *, service: str | None = None, user: str | int | None = None
    ) -> bool: ...
    async def stop_service(self, service: str) -> None: ...
    def _path_kind_check_command(self, path: str, *, require_dir: bool) -> str: ...
    async def run_healthcheck(self, healthcheck: HealthcheckConfig | None = None) -> None: ...
    async def attach(self) -> None: ...
