"""Portable Raidar contract: ``stage/1``, workflow schema 2, ``criteria/1``, ``stage-tree/1``,
the release register and the agent schema.

Pure data and validation: this distribution depends on pydantic and PyYAML only and never
imports ``raidar`` or Harbor.
"""

__version__ = "1.0.0"

CONTRACTS = {"stage": 1, "workflow": 2, "criteria": 1, "stage-tree": 1, "agent": 1}
"""Supported contract/schema versions, keyed by their unversioned names."""
