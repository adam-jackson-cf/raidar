import logging
from collections.abc import Mapping
from typing import Literal

from _typeshed import Incomplete
from harbor.models.trial.config import ServiceVolumeConfig as ServiceVolumeConfig
from pydantic import BaseModel

class ComposeInfraEnvVars(BaseModel):
    main_image_name: str
    context_dir: str
    prebuilt_image_name: str | None = ...
    egress_control_sidecar_image_name: str | None = ...
    egress_control_initial_network_mode: str | None = ...
    egress_control_initial_allowed_hosts: str | None = ...
    cpus: int | None = ...
    memory: str | None = ...
    def to_env_dict(self, include_os_env: bool = False) -> dict[str, str]: ...

def merge_compose_env(
    *,
    base_env: Mapping[str, str] | None = None,
    user_env: Mapping[str, str] | None = None,
    infra_env: Mapping[str, str],
    logger: logging.Logger,
    collision_label: str = "Task/persistent env vars",
) -> dict[str, str]: ...

_LEGACY_LOG_MOUNT_SUFFIXES: Incomplete

def legacy_log_mount_env_vars(
    mounts: list[ServiceVolumeConfig], *, host_value: Literal["source", "target"]
) -> dict[str, str]: ...
