"""Gate diagnostic record shared by every check."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Diagnostic:
    """One finding: ``path:line:col: CODE message``."""

    code: str
    relpath: str
    line: int
    message: str
    col: int = 1
    source: str = "bespoke"

    def render(self) -> str:
        """Return the one-line form printed by the gate."""
        return f"{self.relpath}:{self.line}:{self.col}: {self.code} {self.message}"

    def as_dict(self) -> dict[str, object]:
        """Return the JSON report form."""
        return asdict(self)
