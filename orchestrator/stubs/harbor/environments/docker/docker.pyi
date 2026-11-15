import asyncio
import asyncio.subprocess
import functools
import tempfile
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import override

from _typeshed import Incomplete
from harbor.constants import MAIN_SERVICE_NAME as MAIN_SERVICE_NAME
from harbor.environments.base import (
    BaseEnvironment as BaseEnvironment,
    ExecResult as ExecResult,
    OutputCallback as OutputCallback,
    ServiceOperationsUnsupportedError as ServiceOperationsUnsupportedError,
)
from harbor.environments.capabilities import (
    EnvironmentCapabilities as EnvironmentCapabilities,
    EnvironmentResourceCapabilities as EnvironmentResourceCapabilities,
)
from harbor.environments.definition import (
    require_agent_environment_definition as require_agent_environment_definition,
    should_use_prebuilt_docker_image as should_use_prebuilt_docker_image,
)
from harbor.environments.docker import (
    COMPOSE_BUILD_PATH as COMPOSE_BUILD_PATH,
    COMPOSE_EGRESS_CONTROL_PATH as COMPOSE_EGRESS_CONTROL_PATH,
    COMPOSE_PREBUILT_PATH as COMPOSE_PREBUILT_PATH,
    COMPOSE_WINDOWS_KEEPALIVE_PATH as COMPOSE_WINDOWS_KEEPALIVE_PATH,
    EGRESS_CONTROL_SIDECAR_CONTEXT_PATH as EGRESS_CONTROL_SIDECAR_CONTEXT_PATH,
    ENV_COMPOSE_NAME as ENV_COMPOSE_NAME,
    RESOURCES_COMPOSE_NAME as RESOURCES_COMPOSE_NAME,
    write_env_compose_file as write_env_compose_file,
    write_mounts_compose_file as write_mounts_compose_file,
    write_resources_compose_file as write_resources_compose_file,
)
from harbor.environments.docker.compose_env import (
    ComposeInfraEnvVars as ComposeInfraEnvVars,
    legacy_log_mount_env_vars as legacy_log_mount_env_vars,
    merge_compose_env as merge_compose_env,
)
from harbor.environments.docker.docker_unix import UnixOps as UnixOps
from harbor.environments.docker.runtime import (
    DOCKER_RUNTIME as DOCKER_RUNTIME,
    ContainerRuntime as ContainerRuntime,
)
from harbor.environments.docker.utils import (
    default_docker_platform as default_docker_platform,
    ensure_docker_image_built as ensure_docker_image_built,
)
from harbor.models.environment_type import EnvironmentType as EnvironmentType
from harbor.models.task.config import (
    EnvironmentConfig as EnvironmentConfig,
    NetworkMode as NetworkMode,
    NetworkPolicy as NetworkPolicy,
    TaskOS as TaskOS,
)
from harbor.models.trial.config import ResourceMode as ResourceMode
from harbor.models.trial.paths import TrialPaths as TrialPaths
from harbor.utils.env import resolve_env_vars as resolve_env_vars

_STREAM_READ_CHUNK_SIZE: Incomplete

def _sanitize_docker_image_name(name: str) -> str: ...
def _sanitize_docker_compose_project_name(name: str) -> str: ...

class DockerEnvironment(BaseEnvironment):
    _DOCKER_COMPOSE_BUILD_PATH = COMPOSE_BUILD_PATH
    _DOCKER_COMPOSE_PREBUILT_PATH = COMPOSE_PREBUILT_PATH
    _DOCKER_COMPOSE_EGRESS_CONTROL_PATH = COMPOSE_EGRESS_CONTROL_PATH
    _EGRESS_CONTROL_SIDECAR_CONTEXT_PATH = EGRESS_CONTROL_SIDECAR_CONTEXT_PATH
    _EGRESS_CONTROL_SIDECAR_DOCKER_NAME: str = ...
    _EGRESS_CONTROL_SERVICE_NAME: str = ...
    _EGRESS_CONTROL_SIDECAR_READY_FILE: str = ...
    _EGRESS_CONTROL_KERNEL_PROBE_IMAGE: str = ...
    _EGRESS_CONTROL_KERNEL_PROBE_SCRIPT: str = ...
    _DOCKER_COMPOSE_WINDOWS_KEEPALIVE_PATH = COMPOSE_WINDOWS_KEEPALIVE_PATH
    _image_build_locks: dict[str, asyncio.Lock] = ...
    @classmethod
    def runtime(cls) -> ContainerRuntime: ...
    @classmethod
    def _engine_cmd(cls, *args: str) -> list[str]: ...
    @classmethod
    def _detect_daemon_os(cls) -> str | None: ...
    @staticmethod
    def _detect_windows_containers() -> bool: ...
    @classmethod
    @override
    def preflight(cls) -> None: ...
    _is_windows_container: Incomplete
    _enable_egress_control: Incomplete
    _keep_containers: Incomplete
    _mounts_compose_temp_dir: tempfile.TemporaryDirectory[str] | None
    _mounts_compose_path: Path | None
    _resources_compose_temp_dir: tempfile.TemporaryDirectory[str] | None
    _resources_compose_path: Path | None
    _env_compose_temp_dir: tempfile.TemporaryDirectory[str] | None
    _env_compose_path: Path | None
    _egress_control_services_compose_temp_dir: tempfile.TemporaryDirectory[str] | None
    _egress_control_services_compose_path: Path | None
    _windows_container_name: Incomplete
    _platform: Incomplete
    _env_vars: Incomplete
    _use_prebuilt: bool
    _compose_task_env: dict[str, str]
    def __init__(
        self,
        environment_dir: Path,
        environment_name: str,
        session_id: str,
        trial_paths: TrialPaths,
        task_env_config: EnvironmentConfig,
        keep_containers: bool = False,
        network_policy: NetworkPolicy | None = None,
        phase_network_policies: Sequence[NetworkPolicy] = (),
        *args: object,
        **kwargs: object,
    ) -> None: ...
    @staticmethod
    @override
    def type() -> EnvironmentType: ...
    @staticmethod
    def _requires_egress_control(
        *, startup_network_policy: NetworkPolicy, phase_network_policies: Sequence[NetworkPolicy]
    ) -> bool: ...
    @property
    @override
    def _uses_compose(self) -> bool: ...
    @classmethod
    @override
    def resource_capabilities(cls) -> EnvironmentResourceCapabilities: ...
    @property
    @override
    def capabilities(self) -> EnvironmentCapabilities: ...
    @property
    def _dockerfile_path(self) -> Path: ...
    @property
    def _environment_docker_compose_path(self) -> Path: ...
    @property
    def _docker_compose_paths(self) -> list[Path]: ...
    def _egress_controlled_service_names(self) -> list[str]: ...
    def _write_egress_control_services_compose_file(self) -> Path | None: ...
    def _write_mounts_compose_file(self) -> Path: ...
    def _write_resources_compose_file(self) -> Path | None: ...
    def _write_env_compose_file(self) -> Path: ...
    def _cleanup_mounts_compose_file(self) -> None: ...
    def _cleanup_resources_compose_file(self) -> None: ...
    def _cleanup_env_compose_file(self) -> None: ...
    def _cleanup_egress_control_services_compose_file(self) -> None: ...
    @property
    def _main_image_name(self) -> str: ...
    @classmethod
    def _egress_control_sidecar_dockerfile_path(cls) -> Path: ...
    async def _ensure_egress_control_sidecar_image_built(self) -> None: ...
    def _compose_infra_env_vars(self) -> dict[str, str]: ...
    @override
    def validate_network_policy_support(
        self, network_policy: NetworkPolicy | None = None
    ) -> None: ...
    def _compose_env_vars(self, include_os_env: bool = True) -> dict[str, str]: ...
    @override
    def _validate_definition(self) -> None: ...
    async def _run_docker_compose_command(
        self,
        command: list[str],
        check: bool = True,
        timeout_sec: float | None = None,
        stdin_data: bytes | None = None,
        on_output: OutputCallback | None = None,
    ) -> ExecResult: ...
    @staticmethod
    async def _collect_buffered_output(
        process: asyncio.subprocess.Process,
        *,
        timeout_sec: float | None,
        stdin_data: bytes | None = None,
    ) -> ExecResult: ...
    @staticmethod
    async def _iter_stream_lines(
        stream: asyncio.StreamReader, *, chunk_size: int = ...
    ) -> AsyncIterator[bytes]: ...
    @staticmethod
    async def _collect_streamed_output(
        process: asyncio.subprocess.Process,
        *,
        timeout_sec: float | None,
        stdin_data: bytes | None = None,
        on_output: OutputCallback,
    ) -> ExecResult: ...
    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None: ...
    def _validate_daemon_mode(self) -> None: ...
    async def _validate_image_os(self, image_name: str) -> None: ...
    @classmethod
    @functools.cache
    def _egress_control_kernel_support(cls) -> bool: ...
    @override
    async def start(self, force_build: bool) -> None: ...
    async def _wait_until_compose_exec_ready(self, timeout_sec: int = 90) -> None: ...
    async def _wait_until_egress_sidecar_ready(self, timeout_sec: int = 60) -> None: ...
    @override
    async def prepare_logs_for_host(self) -> None: ...
    @override
    async def stop(self, delete: bool) -> None: ...
    @override
    async def upload_file(self, source_path: Path | str, target_path: str) -> None: ...
    @override
    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None: ...
    _rootless_docker: bool | None = ...
    async def _is_rootless_docker(self) -> bool: ...
    async def _chown_to_host_user(
        self, path: str, recursive: bool = False, service: str | None = None
    ) -> None: ...
    @override
    async def download_file(self, source_path: str, target_path: Path | str) -> None: ...
    @override
    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None: ...
    @override
    async def service_download_file(
        self, source_path: str, target_path: Path | str, *, service: str | None = None
    ) -> None: ...
    @override
    async def service_download_dir(
        self, source_dir: str, target_dir: Path | str, *, service: str | None = None
    ) -> None: ...
    @override
    async def stop_service(self, service: str) -> None: ...
    def _sidecar_platform(self, service: str) -> UnixOps: ...
    @override
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult: ...
    @override
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
    async def _compose_exec(
        self,
        command: str,
        *,
        service: str,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout_sec: int | None,
        user: str | int | None,
    ) -> ExecResult: ...
    @override
    async def _apply_network_policy(self, network_policy: NetworkPolicy) -> None: ...
    @override
    async def attach(self) -> None: ...
