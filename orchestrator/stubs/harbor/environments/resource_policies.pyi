from harbor.environments.capabilities import (
    EnvironmentResourceCapabilities as EnvironmentResourceCapabilities,
)
from harbor.models.trial.config import ResourceMode as ResourceMode

def validate_resource_capabilities(
    *,
    environment_label: str,
    resource_capabilities: EnvironmentResourceCapabilities,
    cpu_enforcement_policy: ResourceMode,
    memory_enforcement_policy: ResourceMode,
) -> None: ...
def validate_resource_values(
    *,
    cpu_enforcement_policy: ResourceMode,
    memory_enforcement_policy: ResourceMode,
    cpus: int | None,
    memory_mb: int | None,
) -> None: ...
