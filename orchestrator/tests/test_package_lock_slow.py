"""A4: each primitive's import closure lies inside its identity inventory (fresh interpreters)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from raidar_contract.agent import AgentName

from raidar.subject.primitive.registry import PRIMITIVE_MODULES
from raidar.subject.workflow.identity import (
    ADAPTER_ENTRIES,
    RAIDAR_DIR,
    RUNNER_ID,
    primitive_inventory,
)

pytestmark = pytest.mark.slow


def _runtime_files(modules: list[str]) -> set[str]:
    """``raidar`` files really loaded by importing ``modules`` in a fresh interpreter."""
    code = (
        "import importlib, json, sys\n"
        f"[importlib.import_module(m) for m in {modules!r}]\n"
        "print(json.dumps(sorted(n for n in sys.modules "
        "if n == 'raidar' or n.startswith('raidar.'))))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    files = set()
    for name in json.loads(done.stdout):
        base = RAIDAR_DIR.parent / name.replace(".", "/")
        path = (
            base.with_suffix(".py") if base.with_suffix(".py").is_file() else base / "__init__.py"
        )
        if path.is_file():
            files.add(path.relative_to(RAIDAR_DIR).as_posix())
    return files


@pytest.mark.parametrize("contract", sorted(PRIMITIVE_MODULES))
def test_each_primitives_runtime_imports_lie_inside_its_inventory(contract: str) -> None:
    loaded = _runtime_files([PRIMITIVE_MODULES[contract]])

    for adapter in AgentName:
        assert loaded <= set(primitive_inventory(contract, adapter)), (
            f"{contract} imports outside its identity inventory: "
            f"{sorted(loaded - set(primitive_inventory(contract, adapter)))}"
        )


def test_the_package_runner_and_each_native_adapter_are_inside_the_inventory() -> None:
    runner = _runtime_files(["raidar.subject.workflow.package", "raidar.subject.workflow.runner"])
    for adapter, entries in ADAPTER_ENTRIES.items():
        loaded = _runtime_files([*entries])
        inventory = set(primitive_inventory(RUNNER_ID, adapter)) | set(
            primitive_inventory("frame/1", adapter)
        )
        assert runner <= set(primitive_inventory(RUNNER_ID, adapter))
        assert loaded <= inventory, sorted(loaded - inventory)
