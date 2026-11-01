"""Portable shared contract vectors; independent of the Raidar runtime."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "contract" / "conformance" / "check.py"
CASES = ROOT / "contract" / "conformance" / "cases.json"


def _load_runner():
    spec = importlib.util.spec_from_file_location("raidar_contract_conformance", RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def conformance():
    return _load_runner(), json.loads(CASES.read_text(encoding="utf-8"))


def _ids():
    return [case["id"] for case in json.loads(CASES.read_text(encoding="utf-8"))]


@pytest.mark.parametrize("case_id", _ids())
def test_contract_conformance(case_id: str, conformance) -> None:
    runner, cases = conformance
    case = next(item for item in cases if item["id"] == case_id)
    runner.check_case(case)
