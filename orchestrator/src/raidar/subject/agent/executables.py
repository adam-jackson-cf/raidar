"""Host executables the agent layer and its commands run: the Harbor CLI and a harness CLI."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def harbor_executable() -> str:
    """Return the project-pinned Harbor CLI, installed next to this interpreter."""
    candidate = Path(sys.executable).parent / "harbor"
    if not (candidate.is_file() and os.access(candidate, os.X_OK)):
        raise FileNotFoundError(f"project-pinned Harbor CLI not found at {candidate}")
    return str(candidate)


def resolve_cli_executable(
    *,
    cli_env_var: str,
    default_binary: str,
    harness_label: str,
) -> str:
    """Resolve a harness CLI executable from env override or PATH."""
    candidate = os.environ.get(cli_env_var) or default_binary
    executable = shutil.which(candidate) if candidate else None
    if executable is None:
        raise FileNotFoundError(
            f"{harness_label} CLI not found or not executable. "
            f"Set {cli_env_var} to an executable or add '{default_binary}' to PATH."
        )
    return executable
