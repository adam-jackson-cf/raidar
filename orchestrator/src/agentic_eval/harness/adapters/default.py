"""Default Harbor-backed harness adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..config import HarnessConfig
from .base import HarnessAdapter


@dataclass(slots=True)
class HarborHarnessAdapter(HarnessAdapter):
    """Adapter that simply proxies to Harbor with minimal validation."""

    def __init__(self, config: HarnessConfig) -> None:
        super().__init__(config)

    def extra_harbor_args(self) -> Iterable[str]:
        return []
