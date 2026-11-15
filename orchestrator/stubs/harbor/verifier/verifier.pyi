import logging
from pathlib import Path
from typing import override

from _typeshed import Incomplete
from harbor.environments.base import BaseEnvironment as BaseEnvironment
from harbor.models.task.task import Task as Task
from harbor.models.trial.paths import EnvironmentPaths as EnvironmentPaths, TrialPaths as TrialPaths
from harbor.models.verifier.result import VerifierResult as VerifierResult
from harbor.utils.env import resolve_env_vars as resolve_env_vars
from harbor.utils.scripts import (
    build_execution_command as build_execution_command,
    needs_chmod as needs_chmod,
    quote_shell_arg as quote_shell_arg,
)
from harbor.verifier.base import BaseVerifier as BaseVerifier

class AddTestsDirError(Exception): ...
class VerifierOutputParseError(Exception): ...
class DownloadVerifierDirError(Exception): ...
class RewardFileNotFoundError(FileNotFoundError): ...
class RewardFileEmptyError(Exception): ...

class Verifier(BaseVerifier):
    _skip_tests_upload: Incomplete
    def __init__(
        self,
        task: Task,
        trial_paths: TrialPaths,
        environment: BaseEnvironment,
        override_env: dict[str, str] | None = None,
        logger: logging.Logger | None = None,
        skip_tests_upload: bool = False,
        verifier_env: dict[str, str] | None = None,
        step_name: str | None = None,
        include_logs: list[str] | None = None,
        exclude_logs: list[str] | None = None,
    ) -> None: ...
    def _parse_reward_text(self) -> dict[str, float | int]: ...
    def _parse_reward_json(self) -> dict[str, float | int]: ...
    def _resolve_tests(self) -> tuple[list[Path], Path, Path]: ...
    @override
    async def verify(self) -> VerifierResult: ...
