"""Fast behavioural proofs for repository hygiene policy."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import pytest

from raidar.subject.package.registry import load_stage_package
from raidar.subject.workflow.identity import package_primitives

ROOT = Path(__file__).resolve().parents[2]


class Checker(Protocol):
    def seam_findings(self, root: Path) -> list[str]: ...


@pytest.fixture
def checker() -> Checker:
    spec = importlib.util.spec_from_file_location(
        "seam_hygiene_proof", ROOT / "scripts/checks/check-test-hygiene.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("Checker", module)


@dataclass(frozen=True)
class Evidence:
    root: Path
    digest: str
    installed: Path
    canary: Path
    record: dict[str, object]


def _evidence(root: Path, name: str = "define") -> Evidence:
    package_root = root / "lab/stages/raidar" / name
    shutil.copytree(ROOT / "lab/stages/raidar" / name, package_root)
    package = load_stage_package(package_root)
    digest = package.digest
    (root / "lab/releases.yaml").write_text(
        f"agents: {{}}\nworkflows: {{}}\nstages:\n  raidar/{name}@1: {digest}\n",
        encoding="utf-8",
    )
    store = root / "lab/stage-checks"
    store.mkdir()
    installed = store / f"{digest}.installed.json"
    installed.write_text(
        json.dumps({"arm64": {"tree_digest": "a" * 64, "wheels": []}}), encoding="utf-8"
    )
    record: dict[str, object] = {
        "package": digest,
        "pack": "b" * 64,
        "primitives": package_primitives([package]),
        "stdout_sha256": "c" * 64,
        "outputs": dict.fromkeys(package.manifest.outputs, "d" * 64),
        "floors": {"disk_free": 1024, "memory_available": 1024},
    }
    canary = store / f"{digest}.json"
    if package.manifest.on_stop is not None:
        canary.write_text(json.dumps(record), encoding="utf-8")
    return Evidence(root, digest, installed, canary, record)


def test_current_content_addressed_seam_evidence_is_accepted(
    tmp_path: Path, checker: Checker
) -> None:
    evidence = _evidence(tmp_path)
    assert not (tmp_path / "var").exists()
    assert checker.seam_findings(evidence.root) == []


def test_stage_without_stop_does_not_require_canary(tmp_path: Path, checker: Checker) -> None:
    evidence = _evidence(tmp_path, "docs-check")
    assert not evidence.canary.exists()
    assert checker.seam_findings(tmp_path) == []


@pytest.mark.parametrize(
    "damage",
    [
        "missing-installed",
        "malformed-installed",
        "empty-installed",
        "missing-wheels",
        "missing-canary",
        "wrong-package",
        "stale-runtime",
        "missing-output",
        "unknown-output",
        "invalid-output-digest",
        "unregistered-record",
        "changed-package",
    ],
)
def test_missing_stale_or_inconsistent_seam_evidence_is_rejected(
    tmp_path: Path, checker: Checker, damage: str
) -> None:
    evidence = _evidence(tmp_path)
    if damage == "missing-installed":
        evidence.installed.unlink()
    elif damage == "malformed-installed":
        evidence.installed.write_text("not json", encoding="utf-8")
    elif damage == "empty-installed":
        evidence.installed.write_text("{}", encoding="utf-8")
    elif damage == "missing-wheels":
        evidence.installed.write_text(
            json.dumps({"arm64": {"tree_digest": "a" * 64}}), encoding="utf-8"
        )
    elif damage == "missing-canary":
        evidence.canary.unlink()
    elif damage == "unregistered-record":
        (evidence.installed.parent / f"{'f' * 64}.installed.json").write_text(
            evidence.installed.read_text(encoding="utf-8"), encoding="utf-8"
        )
    elif damage == "changed-package":
        (tmp_path / "lab/stages/raidar/define/prompt.md").write_text(
            "Changed package prompt.\n", encoding="utf-8"
        )
    else:
        record = dict(evidence.record)
        changes: dict[str, dict[str, object]] = {
            "wrong-package": {"package": "f" * 64},
            "stale-runtime": {"primitives": {"stop/1": "f" * 64}},
            "missing-output": {"outputs": {}},
            "unknown-output": {"outputs": {"unregistered-report": "d" * 64}},
            "invalid-output-digest": {
                "outputs": {"criteria": "not-a-digest", "definition": "d" * 64}
            },
        }
        record.update(changes[damage])
        evidence.canary.write_text(json.dumps(record), encoding="utf-8")
    found = checker.seam_findings(tmp_path)
    assert found
    assert all("SEAM.evidence" in line for line in found)
    assert any("lab/" in line for line in found)


def test_registered_historical_records_are_not_orphan_findings(
    tmp_path: Path, checker: Checker
) -> None:
    evidence = _evidence(tmp_path)
    (tmp_path / "lab/releases.yaml").write_text(
        "agents: {}\nworkflows: {}\nstages:\n"
        f"  raidar/define@1: {'f' * 64}\n  raidar/define@2: {evidence.digest}\n",
        encoding="utf-8",
    )
    (evidence.installed.parent / f"{'f' * 64}.installed.json").write_text(
        evidence.installed.read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert checker.seam_findings(tmp_path) == []
