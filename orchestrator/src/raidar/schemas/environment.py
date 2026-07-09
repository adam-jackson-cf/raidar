"""Canonical environment and dependency inventory schemas."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EnvironmentResourcesConfig(BaseModel):
    """Task runtime resource contract selected by a scenario."""

    model_config = ConfigDict(extra="forbid")

    cpus: int = Field(default=2, gt=0)
    memory_mb: int = Field(default=4096, gt=0)
    storage_mb: int = Field(default=10240, gt=0)


class CapabilityRequirements(BaseModel):
    """Concrete dependency inventory required from or provided by an image."""

    model_config = ConfigDict(extra="forbid")

    runtimes: dict[str, str] = Field(default_factory=dict)
    package_managers: dict[str, str] = Field(default_factory=dict)
    tools: dict[str, str] = Field(default_factory=dict)
    browsers: dict[str, str] = Field(default_factory=dict)


class EnvironmentBuildConfig(BaseModel):
    """Scenario-owned custom Docker build metadata."""

    model_config = ConfigDict(extra="forbid")

    dockerfile: str = Field(description="Repository-relative Dockerfile path")

    @field_validator("dockerfile")
    @classmethod
    def _validate_dockerfile(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("environment.build.dockerfile must not be empty")
        return value


class EnvironmentVerifierConfig(BaseModel):
    """Verifier runner selected by a custom environment."""

    model_config = ConfigDict(extra="forbid")

    runner: str = Field(description="Versioned verifier runner id")

    @field_validator("runner")
    @classmethod
    def _validate_runner(cls, value: str) -> str:
        if "@" not in value:
            raise ValueError("environment.verifier.runner must be a versioned runner id")
        return value


class EnvironmentConfig(BaseModel):
    """Scenario-owned execution environment contract."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["stack_preset", "custom_docker"] = Field(description="Environment selection mode")
    id: str = Field(description="Stack preset id or custom environment id")
    image: str | None = Field(
        default=None,
        description="Custom Docker image tag for custom_docker environments",
    )
    build: EnvironmentBuildConfig | None = Field(
        default=None,
        description="Custom Docker build metadata for custom_docker environments",
    )
    verifier: EnvironmentVerifierConfig | None = Field(
        default=None,
        description="Verifier runner metadata for custom_docker environments",
    )
    capabilities: CapabilityRequirements = Field(
        default_factory=CapabilityRequirements,
        description="Dependency inventory provided by a custom image",
    )
    workdir: str = Field(default="/app", description="Container workspace path")
    requirements: CapabilityRequirements = Field(default_factory=CapabilityRequirements)
    resources: EnvironmentResourcesConfig = Field(default_factory=EnvironmentResourcesConfig)
    allow_internet: bool = True

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> EnvironmentConfig:
        if self.kind == "stack_preset":
            if self.image is not None or self.build is not None or self.verifier is not None:
                raise ValueError(
                    "stack_preset environments must not declare image, build, or verifier"
                )
            if self.capabilities != CapabilityRequirements():
                raise ValueError("stack_preset environments must not declare capabilities")
            return self
        if self.image is None or self.build is None or self.verifier is None:
            raise ValueError(
                "custom_docker environments require image, build.dockerfile, and verifier.runner"
            )
        if self.capabilities == CapabilityRequirements():
            raise ValueError("custom_docker environments require provided capabilities")
        return self

    @field_validator("id")
    @classmethod
    def _validate_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("environment fields must not be empty")
        return value

    @field_validator("workdir")
    @classmethod
    def _validate_workdir(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("environment.workdir must be an absolute container path")
        if ".." in Path(value).parts:
            raise ValueError("environment.workdir must not contain parent traversal")
        return value
