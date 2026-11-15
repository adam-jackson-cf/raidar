from collections.abc import Mapping
from pathlib import Path

def docker_build_context_hash(
    *,
    context: Path,
    dockerfile_path: Path | None = None,
    build_args: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> str: ...
