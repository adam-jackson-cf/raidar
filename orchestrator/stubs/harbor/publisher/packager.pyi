from pathlib import Path

from _typeshed import Incomplete
from harbor.models.task.paths import TaskPaths as TaskPaths

DEFAULT_IGNORES: Incomplete

class Packager:
    @staticmethod
    def collect_files(task_dir: Path) -> list[Path]: ...
    @staticmethod
    def compute_file_hash(file_path: Path) -> str: ...
    @staticmethod
    def compute_content_hash(task_dir: Path) -> tuple[str, list[Path]]: ...
