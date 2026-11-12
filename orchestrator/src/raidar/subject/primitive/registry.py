"""Primitive entry modules whose code digests the experiment lock pins.

Packages may require only ``SUPPORTED_CONTRACTS``; workflow-selected reply and judge contracts
have their own inventory. Package validation refuses unsupported ids before execution.
"""

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
