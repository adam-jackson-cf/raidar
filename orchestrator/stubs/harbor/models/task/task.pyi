from pathlib import Path

from _typeshed import Incomplete
from harbor.models.task.config import TaskConfig as TaskConfig
from harbor.models.task.paths import TaskPaths as TaskPaths
from harbor.models.task.verifier_mode import (
    resolve_effective_verifier_env_config as resolve_effective_verifier_env_config,
)

_CANARY_LINE_RE: Incomplete

def strip_canary(text: str) -> str: ...

class Task:
    _task_dir: Incomplete
    extra_instruction_paths: Incomplete
    extra_instructions: Incomplete
    _extra_instructions: Incomplete
    paths: Incomplete
    config: Incomplete
    name: Incomplete
    instruction: str
    def __init__(
        self,
        task_dir: Path | str,
        extra_instruction_paths: list[Path] | None = None,
        disable_verification: bool = False,
        extra_instructions: list[str] | None = None,
    ) -> None: ...
    @property
    def short_name(self) -> str: ...
    @staticmethod
    def is_valid_dir(task_dir: Path | str, disable_verification: bool = False) -> bool: ...
    @staticmethod
    def _validate_input_paths(config: TaskConfig, paths: TaskPaths) -> None: ...
    @staticmethod
    def _validate_tests(config: TaskConfig, paths: TaskPaths) -> None: ...
    def _read_extra_instructions(self) -> list[str]: ...
    def _append_extra_instructions(self, instruction: str) -> str: ...
    @property
    def has_steps(self) -> bool: ...
    def step_instruction(self, step_name: str) -> str: ...
    @property
    def checksum(self) -> str: ...
    @property
    def task_dir(self) -> Path: ...
