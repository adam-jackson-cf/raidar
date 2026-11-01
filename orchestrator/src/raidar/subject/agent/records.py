"""Host credential readiness and shell-based reset locations owned by the lab runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class Credentials:
    """Whether a host environment can authenticate a harness.

    issue explains failure; file-backed sign-in evidence and authentication-mode environment
    names describe host credential handling, never a portable package call.
    """

    issue: str | None
    session_file: Path | None = None
    session_present: bool = False
    session_used: bool = False
    mode_env: str | None = None


@dataclass(frozen=True, slots=True)
class StatePath:
    """A reset target shell expression ($HOME_DIR/$LOGS_DIR), its baseline and entry kind."""

    target: str
    baseline: str
    kind: Literal["dir", "file"]
