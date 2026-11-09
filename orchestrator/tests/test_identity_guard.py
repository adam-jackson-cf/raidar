"""Fast proofs for identity equality and digest-bound rewrite exclusions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "identity_guard_proofs", ROOT / "scripts/checks/check-identity.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


identity = _load_script()
RECORD = ".omp/identity.json"
SURFACES = {
    "grading_execution": "a" * 64,
    "primitive_code/codex/frame/1": "b" * 64,
    "primitive_code/claude-code/runner/1": "c" * 64,
    "raidar_spec": "d" * 64,
    "scorer/hidden-tests@1": "e" * 64,
    "stage/raidar/verify": "f" * 64,
}


def test_equal_identity_record_is_silent_regardless_of_key_order() -> None:
    assert (
        identity.compare_identities(
            SURFACES, dict(reversed(list(SURFACES.items()))), record_path=RECORD
        )
        == []
    )


@pytest.mark.parametrize("key", SURFACES)
@pytest.mark.parametrize("change", ["changed", "missing", "unexpected"])
def test_identity_differences_name_surface_values_and_reviewed_fix(key: str, change: str) -> None:
    recorded = dict(SURFACES)
    current = dict(SURFACES)
    old = SURFACES[key]
    new = "9" * 64
    if change == "changed":
        current[key] = new
    elif change == "missing":
        del current[key]
        new = "<missing>"
    else:
        del recorded[key]
        old = "<missing>"
        new = current[key]
    lines = identity.compare_identities(recorded, current, record_path=RECORD)
    assert len(lines) == 1
    assert "\n" not in lines[0]
    assert key in lines[0]
    assert old in lines[0]
    assert new in lines[0]
    assert (
        f"declare the identity change: update {RECORD} in its own reviewed commit "
        "with before/after values and schedule revalidation/relock"
    ) in lines[0]


def test_all_identity_differences_are_reported_in_stable_key_order() -> None:
    lines = identity.compare_identities(
        {"z": "before-z", "a": "before-a"},
        {"m": "after-m", "a": "after-a"},
        record_path=RECORD,
    )
    assert len(lines) == 3
    for line, key in zip(lines, ["a", "m", "z"], strict=True):
        assert key in line
    assert lines == identity.compare_identities(
        {"a": "before-a", "z": "before-z"},
        {"a": "after-a", "m": "after-m"},
        record_path=RECORD,
    )


def test_frozen_exclusions_require_latest_release_digest_equality() -> None:
    payloads = [
        "orchestrator/src/raidar/grading/runtime/sitecustomize.py",
        "orchestrator/src/raidar/grading/runtime/raidar_oracle_plugin.py",
        "orchestrator/src/raidar/grading/runtime/coverage.rc",
        "orchestrator/src/raidar/grading/runtime/pytest.ini",
    ]
    released = {
        "lab/stages/raidar/verify": "released-verify",
        "lab/stages/raidar/define": "released-define",
        "lab/stages/raidar/docs-check": "released-docs",
    }
    current = {
        "lab/stages/raidar/verify": "released-verify",
        "lab/stages/raidar/define": "new-release-in-progress",
        "lab/stages/local/draft": "unreleased",
    }
    assert identity.frozen_exclusions(released, current, payloads) == sorted(
        ["lab/stages/raidar/verify", *payloads]
    )
    assert identity.frozen_exclusions({}, {}, payloads) == sorted(payloads)
    assert identity.frozen_exclusions(released, current, payloads + payloads) == sorted(
        ["lab/stages/raidar/verify", *payloads]
    )
