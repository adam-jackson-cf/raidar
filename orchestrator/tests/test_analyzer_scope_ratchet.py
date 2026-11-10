"""Analyzer debt uses the shared finding-level ratchet, never file exemptions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Protocol, TypedDict, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
PATH = "orchestrator/src/raidar/scoring/snapshot_tool/graph.py"


class Finding(TypedDict):
    path: str
    code: str
    fingerprint: str
    count: int


class Baseline(TypedDict):
    schemaVersion: int
    tool: str
    findings: list[Finding]


class FindingRatchet(Protocol):
    def evaluate_baseline(
        self, baseline: Baseline, findings: list[Finding], base_baseline: Baseline
    ) -> tuple[int, list[str]]: ...


@pytest.fixture(scope="module")
def ratchet() -> FindingRatchet:
    script = ROOT / "scripts/checks/check-finding-baseline.py"
    spec = importlib.util.spec_from_file_location("analyzer_finding_ratchet_proofs", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("FindingRatchet", module)


def _finding(fingerprint: str, count: int = 1) -> Finding:
    return {"path": PATH, "code": "PYS251", "fingerprint": fingerprint, "count": count}


def _baseline(findings: list[Finding]) -> Baseline:
    return {"schemaVersion": 1, "tool": "analyzer-scope", "findings": findings}


def test_pys251_ratchet_accepts_unchanged_identified_debt(ratchet: FindingRatchet) -> None:
    findings = [_finding("old-call")]
    assert ratchet.evaluate_baseline(_baseline(findings), findings, _baseline(findings)) == (0, [])


def test_pys251_ratchet_permits_removing_fixed_debt(ratchet: FindingRatchet) -> None:
    base = _baseline([_finding("old-call"), _finding("fixed-call")])
    findings = [_finding("old-call")]
    assert ratchet.evaluate_baseline(_baseline(findings), findings, base) == (0, [])


@pytest.mark.parametrize(
    ("recorded", "current", "base", "diagnostic"),
    [
        (["old-call"], ["old-call", "new-call"], ["old-call"], "BASELINE.new"),
        (["old-call"], ["replacement-call"], ["old-call"], "BASELINE.new"),
        (["old-call", "new-call"], ["old-call", "new-call"], ["old-call"], "BASELINE.refresh"),
        (["old-call"], [], ["old-call"], "BASELINE.stale"),
    ],
)
def test_pys251_ratchet_refuses_new_replaced_refreshed_or_stale_findings(
    ratchet: FindingRatchet,
    recorded: list[str],
    current: list[str],
    base: list[str],
    diagnostic: str,
) -> None:
    status, findings = ratchet.evaluate_baseline(
        _baseline([_finding(mark) for mark in recorded]),
        [_finding(mark) for mark in current],
        _baseline([_finding(mark) for mark in base]),
    )
    assert status == 1
    assert any(diagnostic in line and PATH in line and "PYS251" in line for line in findings)


def test_pys251_ratchet_refuses_more_instances_of_the_same_finding(ratchet: FindingRatchet) -> None:
    baseline = _baseline([_finding("old-call")])
    status, findings = ratchet.evaluate_baseline(baseline, [_finding("old-call", 2)], baseline)
    assert status == 1
    assert any("BASELINE.new" in line and PATH in line and "PYS251" in line for line in findings)
