import logging
from collections.abc import Collection, Sequence
from pathlib import Path

from _typeshed import Incomplete
from harbor.environments.base import (
    BaseEnvironment as BaseEnvironment,
    EnvironmentPath as EnvironmentPath,
)
from harbor.models.task.artifacts import (
    effective_artifact_service as effective_artifact_service,
    is_convention_entry as is_convention_entry,
    source_host_path as source_host_path,
    with_convention_entry as with_convention_entry,
)
from harbor.models.trial.artifact_manifest import (
    ArtifactManifest as ArtifactManifest,
    ArtifactManifestEntry as ArtifactManifestEntry,
)
from harbor.models.trial.config import ArtifactConfig as ArtifactConfig
from harbor.utils.path_safety import directory_move_plan as directory_move_plan

_MANIFEST_FILENAME: str

def artifact_host_path(artifacts_dir: Path, artifact: ArtifactConfig) -> Path: ...
def _relative_host_destination(destination: str) -> Path: ...

class ArtifactHandler:
    artifacts: Incomplete
    logger: Incomplete
    _claimed_targets: list[tuple[Path, str]]
    def __init__(
        self, *, artifacts: Sequence[str | ArtifactConfig], logger: logging.Logger
    ) -> None: ...
    def begin_collection(self) -> None: ...
    def _find_conflicting_claim(self, target: Path, source: str) -> str | None: ...
    @staticmethod
    def move_dir_contents(src: Path, dst: Path) -> None: ...
    @staticmethod
    def move_dir_contents_preserving(
        src: Path, dst: Path, *, preserve_dirs: Sequence[Path]
    ) -> None: ...
    async def download_artifacts(
        self,
        source_env: BaseEnvironment,
        artifacts_dir: Path,
        *,
        source_artifacts_dir: EnvironmentPath,
        artifacts: Sequence[str | ArtifactConfig] | None = None,
        services: Collection[str] | None = None,
    ) -> ArtifactManifest: ...
    async def upload_artifacts(
        self,
        target_env: BaseEnvironment,
        artifacts_dir: Path,
        *,
        source_artifacts_dir: EnvironmentPath,
        target_artifacts_dir: EnvironmentPath,
        artifacts: Sequence[str | ArtifactConfig] | None = None,
    ) -> None: ...
    def sidecar_services(
        self, artifacts: Sequence[str | ArtifactConfig] | None = None
    ) -> set[str]: ...
    def _normalized_artifacts(
        self, artifacts: Sequence[str | ArtifactConfig] | None, convention_source: str
    ) -> list[ArtifactConfig]: ...
    async def _download_artifact(
        self,
        *,
        source_env: BaseEnvironment,
        artifacts_dir: Path,
        artifact: ArtifactConfig,
        convention_source: str,
    ) -> ArtifactManifestEntry: ...
    def _record_mounted_artifacts_dir(
        self,
        *,
        source: str,
        target: Path,
        manifest_destination: str,
        service: str | None,
        exclude: Sequence[str],
    ) -> ArtifactManifestEntry: ...
    def _host_path(
        self, artifacts_dir: Path, artifact: ArtifactConfig, convention_source: str
    ) -> Path: ...
    def _upload_target_source(
        self, artifact: ArtifactConfig, *, source_convention: str, target_convention: str
    ) -> str: ...
    @staticmethod
    def _environment_path_str(path: EnvironmentPath) -> str: ...
    def _manifest_destination(self, artifacts_dir: Path, target: Path) -> str: ...
    def _write_manifest(
        self, artifacts_dir: Path, new_entries: list[ArtifactManifestEntry]
    ) -> ArtifactManifest: ...
