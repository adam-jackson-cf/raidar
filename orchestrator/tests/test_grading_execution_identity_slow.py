"""Real host and sandbox-plugin imports stay inside grading's static closure."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from raidar.contract.source_imports import import_closure
from raidar.grading.identity import grading_roots

pytestmark = pytest.mark.slow


def test_every_loaded_local_module_is_in_the_complete_grading_import_closure() -> None:
    roots = grading_roots()
    closure = set().union(*(import_closure(module) for module in roots))
    code = (
        "import importlib, json, sys\n"
        f"[importlib.import_module(module) for module in {roots!r}]\n"
        "print(json.dumps(sorted(name for name in sys.modules "
        "if name.split('.')[0] in ('raidar', 'raidar_contract'))))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    loaded = set(json.loads(done.stdout))
    assert loaded <= closure, sorted(loaded - closure)
