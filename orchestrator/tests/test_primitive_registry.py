"""The primitive registry: supported contract ids map to modules; nothing else is importable."""

from __future__ import annotations

import pytest

from raidar.subject.primitive import stop
from raidar.subject.primitive.registry import (
    PRIMITIVE_MODULES,
    SUPPORTED_CONTRACTS,
    primitive_module,
)


def test_every_supported_contract_resolves_to_its_primitive_module() -> None:
    assert primitive_module("stop/1") is stop
    for contract in SUPPORTED_CONTRACTS:
        module = primitive_module(contract)
        assert module.__name__ == PRIMITIVE_MODULES[contract]


@pytest.mark.parametrize("contract", ["stop/2", "plugin/1", "raidar.subject.primitive.stop", ""])
def test_an_unsupported_contract_is_refused_not_imported(contract: str) -> None:
    with pytest.raises(ValueError, match="unsupported primitive contract"):
        primitive_module(contract)
