import logging
from pathlib import Path
from typing import NamedTuple

from harbor.environments.base import BaseEnvironment as BaseEnvironment
from harbor.environments.capabilities import (
    EnvironmentResourceCapabilities as EnvironmentResourceCapabilities,
)
from harbor.environments.resource_policies import (
    validate_resource_capabilities as validate_resource_capabilities,
)
from harbor.models.environment_type import EnvironmentType as EnvironmentType
from harbor.models.task.config import EnvironmentConfig as EnvironmentConfig
from harbor.models.trial.config import (
    EnvironmentConfig as TrialEnvironmentConfig,
    ResourceMode as ResourceMode,
)
from harbor.models.trial.paths import TrialPaths as TrialPaths
from harbor.utils.import_path import import_class as import_class, import_symbol as import_symbol

class _EnvEntry(NamedTuple):
    module: str
    class_name: str
    pip_extra: str | None

_ENVIRONMENT_REGISTRY: dict[EnvironmentType, _EnvEntry]

def _load_environment_class(env_type: EnvironmentType) -> type[BaseEnvironment]: ...

class EnvironmentFactory:
    @classmethod
    def validate_resource_policies(cls, config: TrialEnvironmentConfig) -> None: ...
    @classmethod
    def create_environment_from_import_path(
        cls,
        import_path: str,
        environment_dir: Path,
        environment_name: str,
        session_id: str,
        trial_paths: TrialPaths,
        task_env_config: EnvironmentConfig,
        logger: logging.Logger | None = None,
        **kwargs: object,
    ) -> BaseEnvironment: ...
    @classmethod
    def create_environment_from_config(
        cls,
        config: TrialEnvironmentConfig,
        environment_dir: Path,
        environment_name: str,
        session_id: str,
        trial_paths: TrialPaths,
        task_env_config: EnvironmentConfig,
        logger: logging.Logger | None = None,
        **kwargs: object,
    ) -> BaseEnvironment: ...
