"""The file reader the measurement program is given (rule PYS251).

The program never opens files itself: its entry receives a ``SourceReader`` and passes it
down. In the tool interpreter that reader is ``raidar.contract.file_io``, loaded by the
bootstrap in ``raidar.scoring.code_quality.measurement``; in process it is the same module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class SourceReader(Protocol):
    def load_text(
        self, path: Path, *, encoding: str | None = None, errors: str | None = None
    ) -> str: ...

    def load_bytes(self, path: Path) -> bytes: ...
