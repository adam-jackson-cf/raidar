import abc
import logging
from abc import ABC, abstractmethod
from typing import Any

from _typeshed import Incomplete
from harbor.environments.base import BaseEnvironment as BaseEnvironment
from harbor.models.task.task import Task as Task
from harbor.models.trial.paths import TrialPaths as TrialPaths
from harbor.models.verifier.result import VerifierResult as VerifierResult

class BaseVerifier(ABC, metaclass=abc.ABCMeta):
    task: Incomplete
    trial_paths: Incomplete
    environment: Incomplete
    override_env: dict[str, str]
    logger: logging.Logger
    verifier_env: Incomplete
    step_name: Incomplete
    include_logs: Incomplete
    exclude_logs: Incomplete
    def __init__(
        self,
        *,
        task: Task,
        trial_paths: TrialPaths,
        environment: BaseEnvironment,
        override_env: dict[str, str] | None = None,
        logger: logging.Logger | None = None,
        verifier_env: dict[str, str] | None = None,
        step_name: str | None = None,
        include_logs: list[str] | None = None,
        exclude_logs: list[str] | None = None,
        **_: Any,
    ) -> None: ...
    @abstractmethod
    async def verify(self) -> VerifierResult: ...
