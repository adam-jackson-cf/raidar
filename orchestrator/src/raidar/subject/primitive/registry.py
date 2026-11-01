"""Which primitive implements each supported contract id (``frame/1``, ``stop/1`` ...).

A package's ``requires`` names contract ids; only ids listed here are supported. A package never
selects code: an unsupported id is refused, not imported.
"""

from __future__ import annotations

import importlib
from types import ModuleType

PRIMITIVE_MODULES: dict[str, str] = {
    "frame/1": "raidar_contract.package.tokens",
    "install/1": "raidar.subject.primitive.install",
    "setup/1": "raidar.subject.primitive.setup",
    "gate/1": "raidar.subject.primitive.gate",
    "stop/1": "raidar.subject.primitive.stop",
    "outputs/1": "raidar.subject.primitive.outputs",
}

WORKFLOW_PRIMITIVE_MODULES: dict[str, str] = {
    "acceptance-reply/1": "raidar.subject.workflow.acceptance_reply",
    "jev-judge/1": "raidar.subject.workflow.package_judge",
}
"""Raidar-owned primitives a *workflow* opts into (``build.reply``, ``judge``): no package may
require them, so they are not in ``SUPPORTED_CONTRACTS``; their code identity is still locked."""

SUPPORTED_CONTRACTS = frozenset(PRIMITIVE_MODULES)


def primitive_module(contract_id: str) -> ModuleType:
    """The module implementing ``contract_id``; unsupported ids raise ``ValueError``."""
    try:
        module = PRIMITIVE_MODULES[contract_id]
    except KeyError:
        raise ValueError(f"unsupported primitive contract {contract_id!r}") from None
    return importlib.import_module(module)
