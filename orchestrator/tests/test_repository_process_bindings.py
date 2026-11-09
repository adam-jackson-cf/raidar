"""Process binding and wait-boundary regressions for the hygiene checker."""

from __future__ import annotations

import ast
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]


class WaitVisitor(Protocol):
    lines: list[int]

    def visit(self, node: ast.AST) -> object: ...


WaitVisitorFactory = Callable[[], WaitVisitor]


class Checker(Protocol):
    ProcessWaits: WaitVisitorFactory


@pytest.fixture
def checker() -> Checker:
    spec = importlib.util.spec_from_file_location(
        "process_bindings_checker", ROOT / "scripts/checks/check-test-hygiene.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("Checker", module)


@pytest.mark.parametrize(
    ("source", "accepted"),
    [
        ("import subprocess as sp\nsp.Popen(['tool']).wait(2)\n", True),
        ("from subprocess import Popen\np: Popen = Popen(['tool'])\np.wait()\n", False),
        ("import subprocess\np = subprocess.Popen(['tool'])\np.communicate(None, 2)\n", True),
        ("import subprocess\nsubprocess.run(['tool'], timeout=-2)\n", False),
        ("import subprocess\nsubprocess.run(['tool'], timeout=+0)\n", False),
        (
            "import subprocess\nwith subprocess.Popen(['tool']) as p:\n    p.wait()\n",
            False,
        ),
        (
            "import subprocess\nclass Probe:\n"
            "    def run(self):\n"
            "        self.p = subprocess.Popen(['tool'])\n"
            "        self.p.communicate()\n",
            False,
        ),
        (
            "from asyncio import create_subprocess_exec as spawn, wait_for as bound\n"
            "async def probe():\n"
            "    p = await spawn('tool')\n"
            "    await bound(fut=p.wait(), timeout=2)\n",
            True,
        ),
        (
            "import asyncio\nasync def probe():\n"
            "    p = await asyncio.create_subprocess_exec('tool')\n"
            "    await asyncio.wait_for(p.wait(), timeout=0)\n",
            False,
        ),
        (
            "import asyncio\nimport subprocess\nasync def probe():\n"
            "    await asyncio.wait_for(subprocess.run(['tool']), timeout=2)\n",
            False,
        ),
        (
            "import subprocess\n"
            "def first():\n    p = subprocess.Popen(['tool'])\n    p.wait(timeout=2)\n"
            "def second(p):\n    p.wait()\n",
            True,
        ),
        (
            "from subprocess import run\ndef application(run):\n    run()\n",
            True,
        ),
        (
            "import asyncio\nasync def probe(p: asyncio.subprocess.Process):\n"
            "    await p.communicate()\n",
            False,
        ),
    ],
)
def test_only_real_process_waits_need_effective_bounds(
    checker: Checker, source: str, *, accepted: bool
) -> None:
    visitor = checker.ProcessWaits()
    visitor.visit(ast.parse(source))
    assert (visitor.lines == []) is accepted
