from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

class BridgeKind(StrEnum):
    ACP = "acp"

class BridgeConfig(BaseModel):
    kind: BridgeKind
    prompt_path: Path | None = ...
    kwargs: dict[str, Any] = ...
