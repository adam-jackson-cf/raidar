import logging
from typing import Any

from harbor.environments.base import BaseEnvironment as BaseEnvironment
from harbor.models.task.task import Task as Task
from harbor.models.trial.config import VerifierConfig as VerifierConfig
from harbor.models.trial.paths import TrialPaths as TrialPaths
from harbor.utils.import_path import import_class as import_class
from harbor.verifier.base import BaseVerifier as BaseVerifier
from harbor.verifier.verifier import Verifier as Verifier

class VerifierFactory:
    @classmethod
    def create_verifier_from_import_path(
        cls,
        import_path: str,
        *,
        task: Task,
        trial_paths: TrialPaths,
        environment: BaseEnvironment,
        override_env: dict[str, str] | None = None,
        logger: logging.Logger | None = None,
        verifier_env: dict[str, str] | None = None,
        step_name: str | None = None,
        **kwargs: Any,
    ) -> BaseVerifier: ...
    @classmethod
    def create_verifier_from_config(
        cls,
        config: VerifierConfig,
        *,
        task: Task,
        trial_paths: TrialPaths,
        environment: BaseEnvironment,
        override_env: dict[str, str] | None = None,
        logger: logging.Logger | None = None,
        verifier_env: dict[str, str] | None = None,
        step_name: str | None = None,
        skip_tests_upload: bool = False,
        **kwargs: Any,
    ) -> BaseVerifier: ...
