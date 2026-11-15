import abc
from abc import ABC, abstractmethod
from collections.abc import Callable as Callable
from pathlib import Path
from typing import Any

from _typeshed import Incomplete
from harbor.models.dataset_item import DownloadedDatasetItem as DownloadedDatasetItem
from harbor.models.registry import (
    DatasetMetadata as DatasetMetadata,
    DatasetSummary as DatasetSummary,
)
from harbor.tasks.client import (
    TaskClient as TaskClient,
    TaskDownloadResult as TaskDownloadResult,
    TaskIdType as TaskIdType,
)

class UnavailableDatasetTasksError(Exception):
    dataset_ref: Incomplete
    missing_task_count: Incomplete
    def __init__(self, dataset_ref: str, missing_task_count: int) -> None: ...

class EmptyDatasetError(Exception):
    dataset_ref: Incomplete
    def __init__(self, dataset_ref: str) -> None: ...

def resolve_version(versions: list[str]) -> str: ...

class BaseRegistryClient(ABC, metaclass=abc.ABCMeta):
    _url: Incomplete
    _path: Incomplete
    _task_client: Incomplete
    def __init__(self, url: str | None = None, path: Path | None = None) -> None: ...
    @abstractmethod
    async def _get_dataset_metadata(self, name: str) -> DatasetMetadata: ...
    @abstractmethod
    async def list_datasets(self) -> list[DatasetSummary]: ...
    async def get_dataset_metadata(self, name: str) -> DatasetMetadata: ...
    async def download_dataset_files(
        self, metadata: DatasetMetadata, overwrite: bool = False, output_dir: Path | None = None
    ) -> dict[str, Path]: ...
    async def _after_dataset_download(self, metadata: DatasetMetadata) -> None: ...
    async def download_dataset(
        self,
        name: str,
        overwrite: bool = False,
        output_dir: Path | None = None,
        export: bool = False,
        on_task_download_start: Callable[[TaskIdType], Any] | None = None,
        on_task_download_complete: Callable[[TaskIdType, TaskDownloadResult], Any] | None = None,
        on_total_known: Callable[[int], Any] | None = None,
    ) -> list[DownloadedDatasetItem]: ...
