"""Assessor-owned rescore settings file (``raidar assess --rescore --config FILE``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, ValidationError

from raidar.scoring.framework.inventory import InventoryPolicy
from raidar.scoring.framework.manifest import SdkModel
from raidar.scoring.framework.rescore import RescoreError


class RescoreConfig(SdkModel):
    """Assessor-owned settings for a rescore (``raidar assess --rescore --config FILE``).

    ``inventory_policy`` sets the assessor globs for generated and test files;
    ``scorers`` maps a scorer reference to its config. Both feed the key.
    """

    inventory_policy: InventoryPolicy = Field(default_factory=InventoryPolicy)
    scorers: dict[str, dict[str, Any]] = Field(default_factory=dict)


def load_rescore_config(path: Path) -> RescoreConfig:
    """Read a YAML (or JSON) rescore config file."""
    try:
        loaded = yaml.safe_load(path.read_text()) or {}
        return RescoreConfig.model_validate(loaded)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise RescoreError(f"invalid rescore config {path}: {exc}") from exc
