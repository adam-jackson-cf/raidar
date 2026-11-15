from collections.abc import Callable as Callable
from typing import Any

from _typeshed import Incomplete
from pydantic import BaseModel
from pydantic.fields import FieldInfo as FieldInfo

class Cli:
    flag: Incomplete
    format: Incomplete
    fallback: Incomplete
    sensitive: Incomplete

class Env:
    name: Incomplete
    fallback: Incomplete
    true_value: Incomplete
    false_value: Incomplete
    def __init__(
        self,
        name: str,
        *,
        fallback: str | None = None,
        true_value: str = "true",
        false_value: str = "false",
    ) -> None: ...

class AgentOptions(BaseModel): ...

class InstalledAgentOptions(AgentOptions):
    version: str | None = ...
    prompt_template_path: str | None = ...
    config: Any = ...

def apply_env_fallbacks(
    model: type[AgentOptions], data: dict[str, Any], get_env: Callable[[str], str | None]
) -> dict[str, Any]: ...
def cli_annotation(field: FieldInfo) -> Cli | None: ...
def env_annotation(field: FieldInfo) -> Env | None: ...
def compile_cli_from_options(options: AgentOptions) -> list[str]: ...
def sensitive_option_values(options: AgentOptions) -> list[str]: ...
def compile_env_from_options(options: AgentOptions) -> dict[str, str]: ...
