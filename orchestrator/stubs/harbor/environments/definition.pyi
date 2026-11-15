from collections.abc import Sequence
from pathlib import Path

from _typeshed import Incomplete

DOCKERFILE_NAME: str
COMPOSE_FILE_NAME: str

def has_agent_environment_definition(
    environment_dir: Path,
    *,
    docker_image: str | None = None,
    extra_docker_compose_paths: Sequence[Path] | None = None,
) -> bool: ...
def should_use_prebuilt_docker_image(
    environment_dir: Path, *, docker_image: str | None, force_build: bool
) -> bool: ...
def should_upload_environment_dir(environment_dir: Path, *, docker_image: str | None) -> bool: ...
def require_agent_environment_definition(
    environment_dir: Path,
    *,
    docker_image: str | None = None,
    extra_docker_compose_paths: Sequence[Path] | None = None,
) -> None: ...

_CONTENT_HASH_IGNORE_NAMES: Incomplete
SNAPSHOT_HASH_LEN: int

def environment_content_hash(
    environment_dir: Path, *, docker_image: str | None = None, truncate: int = 32
) -> str: ...
def parse_dockerfile_workdir(dockerfile_path: Path) -> str | None: ...
def effective_exec_cwd(
    cwd: str | None, config_workdir: str | None, dockerfile_workdir: str | None
) -> str | None: ...
