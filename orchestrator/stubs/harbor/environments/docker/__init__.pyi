from pathlib import Path

from _typeshed import Incomplete
from harbor.models.trial.config import ServiceVolumeConfig as ServiceVolumeConfig

COMPOSE_DIR: Incomplete
COMPOSE_BUILD_PATH: Incomplete
COMPOSE_PREBUILT_PATH: Incomplete
COMPOSE_NO_NETWORK_PATH: Incomplete
COMPOSE_EGRESS_CONTROL_PATH: Incomplete
EGRESS_CONTROL_SIDECAR_CONTEXT_PATH: Incomplete
COMPOSE_WINDOWS_KEEPALIVE_PATH: Incomplete
RESOURCES_COMPOSE_NAME: str
ENV_COMPOSE_NAME: str

def write_env_compose_file(path: Path, env: dict[str, str]) -> Path: ...
def write_mounts_compose_file(path: Path, mounts: list[ServiceVolumeConfig]) -> Path: ...
def write_resources_compose_file(
    path: Path,
    *,
    cpu_request: int | None = None,
    cpu_limit: int | None = None,
    memory_request_mb: int | None = None,
    memory_limit_mb: int | None = None,
) -> Path: ...
def self_bind_mount(mount: ServiceVolumeConfig) -> ServiceVolumeConfig: ...
