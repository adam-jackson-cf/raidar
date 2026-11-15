import functools
import logging
from collections.abc import Mapping
from pathlib import Path

from harbor.utils.container_cache import docker_build_context_hash as docker_build_context_hash

_BUILD_LOCK_FILENAME: str

@functools.cache
def _docker_build_cache_dir() -> Path: ...
def _cache_dir_name(docker_name: str, hash_key: str) -> str: ...
def _compute_image_name(docker_name: str, hash_key: str) -> str: ...
async def default_docker_platform() -> str: ...
async def remote_docker_image_exists(
    image_url: str, logger: logging.Logger | None = None
) -> bool: ...
async def build_docker_image_with_buildx(
    *,
    docker_image_name: str,
    context: Path,
    dockerfile_path: Path,
    build_log_path: Path,
    build_args: Mapping[str, str],
    platform: str,
    push: bool = False,
    ignore_existing_tag_push_error: bool = False,
    timeout_sec: float | None = None,
) -> None: ...
async def docker_image_exists(docker_image_name: str) -> bool: ...
async def ensure_docker_image_built(
    *,
    docker_name: str,
    docker_build_context: Path,
    dockerfile_path: Path,
    build_args: Mapping[str, str],
    platform: str,
    push: bool = False,
    ignore_existing_tag_push_error: bool = False,
    timeout_sec: float | None = None,
    logger: logging.Logger | None = None,
) -> str: ...
