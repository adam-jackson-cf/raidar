from pathlib import Path

from harbor.models.task.id import (
    GitTaskId as GitTaskId,
    LocalTaskId as LocalTaskId,
    PackageTaskId as PackageTaskId,
)
from pydantic import BaseModel

class DownloadedDatasetItem(BaseModel):
    id: GitTaskId | LocalTaskId | PackageTaskId
    downloaded_path: Path
