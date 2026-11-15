from collections.abc import Callable as Callable
from pathlib import Path
from typing import Any

from _typeshed import Incomplete
from harbor.constants import ARCHIVE_FILENAME as ARCHIVE_FILENAME, TASK_CACHE_DIR as TASK_CACHE_DIR
from harbor.models.task.id import (
    GitTaskId as GitTaskId,
    LocalTaskId as LocalTaskId,
    PackageTaskId as PackageTaskId,
    normalize_git_task_path as normalize_git_task_path,
)
from harbor.utils.logger import logger as logger
from pydantic import BaseModel

TaskIdType = GitTaskId | LocalTaskId | PackageTaskId
_GIT_COMMIT_RE: Incomplete

def _is_resolved_git_commit_id(git_commit_id: str | None) -> bool: ...

class TaskDownloadConfig(BaseModel):
    git_commit_id: str | None = ...
    source_path: Path
    target_path: Path

class TaskDownloadResult(BaseModel):
    path: Path
    download_time_sec: float
    cached: bool
    content_hash: str | None = ...
    resolved_git_commit_id: str | None = ...

class BatchDownloadResult(BaseModel):
    results: list[TaskDownloadResult]
    total_time_sec: float
    @property
    def paths(self) -> list[Path]: ...

class _ResolvedPackage(BaseModel):
    id: str
    archive_path: str
    content_hash: str

class TaskClient:
    _lfs_warning_logged: bool = ...
    async def _run_git_stdout(self, *args: str | Path, cwd: Path | None = None) -> str: ...
    def _repo_uses_lfs(self, repo_dir: Path) -> bool: ...
    async def _pull_lfs_files(
        self, repo_dir: Path, task_download_configs: list[TaskDownloadConfig]
    ) -> None: ...
    def _materialize_task_tree(self, source_root: Path, target_path: Path) -> None: ...
    def _copy_task_source_to_target(
        self, source_path: Path, target_path: Path, *, repo_dir: Path
    ) -> None: ...
    async def _download_tasks_from_git_url(
        self, git_url: str, task_download_configs: list[TaskDownloadConfig]
    ) -> dict[Path, str]: ...
    async def _resolve_package_version(self, task_id: PackageTaskId) -> _ResolvedPackage: ...
    async def _download_package_tasks(
        self,
        package_task_ids: list[PackageTaskId],
        overwrite: bool = False,
        output_dir: Path | None = None,
        export: bool = False,
        max_concurrency: int = 100,
        on_task_download_start: Callable[[TaskIdType], Any] | None = None,
        on_task_download_complete: Callable[[TaskIdType, TaskDownloadResult], Any] | None = None,
    ) -> dict[PackageTaskId, TaskDownloadResult]: ...
    async def _download_local_tasks(
        self,
        task_ids: list[LocalTaskId],
        on_task_download_start: Callable[[TaskIdType], Any] | None = None,
        on_task_download_complete: Callable[[TaskIdType, TaskDownloadResult], Any] | None = None,
    ) -> dict[LocalTaskId, TaskDownloadResult]: ...
    async def _download_git_tasks(
        self,
        task_ids: list[GitTaskId],
        overwrite: bool,
        output_dir: Path,
        export: bool = False,
        on_task_download_start: Callable[[TaskIdType], Any] | None = None,
        on_task_download_complete: Callable[[TaskIdType, TaskDownloadResult], Any] | None = None,
    ) -> dict[GitTaskId, TaskDownloadResult]: ...
    async def download_tasks(
        self,
        task_ids: list[GitTaskId | LocalTaskId | PackageTaskId],
        overwrite: bool = False,
        output_dir: Path | None = None,
        export: bool = False,
        on_task_download_start: Callable[[TaskIdType], Any] | None = None,
        on_task_download_complete: Callable[[TaskIdType, TaskDownloadResult], Any] | None = None,
    ) -> BatchDownloadResult: ...
