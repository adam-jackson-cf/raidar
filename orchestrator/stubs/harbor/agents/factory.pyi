from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent as BaseAgent
from harbor.agents.installed.acp_registry import (
    is_acp_registry_shorthand as is_acp_registry_shorthand,
    registry_spec_from_agent_name as registry_spec_from_agent_name,
)
from harbor.models.agent.name import AgentName as AgentName
from harbor.models.trial.config import AgentConfig as AgentConfig
from harbor.utils.env import resolve_env_vars as resolve_env_vars
from harbor.utils.import_path import import_class as import_class

def _import_agent_class(import_path: str) -> type[BaseAgent]: ...
def _constructor_kwargs(config: AgentConfig, kwargs: dict[str, Any]) -> dict[str, Any]: ...

class AgentFactory:
    _AGENT_MAP: dict[AgentName, str] = ...
    @classmethod
    def registered_names(cls) -> list[str]: ...
    @classmethod
    def get_agent_class(cls, name: AgentName) -> type[BaseAgent]: ...
    @classmethod
    def get_agent_class_from_config(cls, config: AgentConfig) -> type[BaseAgent]: ...
    @classmethod
    def run_preflight(cls, config: AgentConfig) -> None: ...
    @classmethod
    def create_agent_from_name(
        cls, name: AgentName, logs_dir: Path, model_name: str | None = None, **kwargs: object
    ) -> BaseAgent: ...
    @classmethod
    def create_agent_from_import_path(
        cls, import_path: str, logs_dir: Path, model_name: str | None = None, **kwargs: object
    ) -> BaseAgent: ...
    @classmethod
    def create_agent_from_config(
        cls, config: AgentConfig, logs_dir: Path, **kwargs: object
    ) -> BaseAgent: ...
