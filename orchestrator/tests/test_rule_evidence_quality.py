"""Real-process fail-closed rule-evidence canaries, confined to temporary checkouts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.quality
ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = "scripts/checks/validate-rule-coverage.py"
LEDGER = ".omp/rule-coverage.json"
CATALOGUE = ".omp/guardrail-rules.json"
HOOK_CHECK = "scripts/checks/check-hook-installation.py"


def _environment() -> dict[str, str]:
    """Keep child git commands independent of agent or enclosing hook context."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != "AGENT" and not key.startswith("GIT_")
    }
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    return environment


def _run(argv: list[str], root: Path, *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=root,
        env=_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _validate(root: Path) -> subprocess.CompletedProcess[str]:
    assert (root / VALIDATOR).is_file(), f"missing real validator: {VALIDATOR}"
    assert (root / LEDGER).is_file(), f"missing real ledger: {LEDGER}"
    return _run(
        [
            sys.executable,
            VALIDATOR,
            "--root",
            ".",
            "--catalog",
            CATALOGUE,
            "--ledger",
            LEDGER,
        ],
        root,
        timeout=240,
    )


def _ledger(root: Path) -> dict[str, Any]:
    path = root / LEDGER
    assert path.is_file(), f"missing real ledger: {LEDGER}"
    ledger: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return ledger


def _row(root: Path, rule_id: str) -> dict[str, Any]:
    return next(row for row in _ledger(root)["rules"] if row["id"] == rule_id)


@pytest.fixture
def isolated_repository(tmp_path: Path) -> Path:
    """Copy live definitions, not environments, artifacts, Git state or unrelated docs."""
    destination = tmp_path / "repository"
    shutil.copytree(
        ROOT,
        destination,
        ignore=shutil.ignore_patterns(
            ".git",
            "var",
            ".venv",
            "node_modules",
            "__pycache__",
            ".cache",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            "docs",
        ),
    )
    # The binding contract explicitly authorizes these exclusion decision records.
    decisions = ROOT / "docs/reboot/evidence"
    for family in ("guardrails", "release", "analyzer"):
        for source in decisions.glob(f"decide-{family}-*.md"):
            target = destination / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    return destination


def test_real_repository_ledger_passes_executed_validator() -> None:
    """The real ledger must prove all rules, not merely pass structure-only planning."""
    result = _validate(ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    catalogue = json.loads((ROOT / ".omp/guardrail-rules.json").read_text(encoding="utf-8"))
    assert f"{len(catalogue['rules'])} rules" in result.stdout
    assert "STRUCTURE_ONLY" not in result.stdout


def test_disabled_checker_fails_its_violating_canary(isolated_repository: Path) -> None:
    """An always-green real checker must not count as implemented enforcement."""
    row = _row(isolated_repository, "PYQ107")
    canary = row["canary"]
    scripts = [
        isolated_repository / part
        for part in canary["command"]
        if part.endswith(".py") and "{fixture}" not in part
    ]
    assert scripts, "PYQ107 canary must invoke its real Python checker"
    checker = scripts[0]
    assert checker.resolve().is_relative_to(isolated_repository.resolve())
    assert checker.is_file(), checker
    checker.write_text("raise SystemExit(0)\n", encoding="utf-8")

    result = _validate(isolated_repository)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "PYQ107: violating canary passed" in result.stderr


def test_missing_fixture_refuses_rule_evidence(isolated_repository: Path) -> None:
    """The actual ledger fixture must exist, not just be named in a row."""
    row = _row(isolated_repository, "PYQ107")
    relative = row["canary"]["violating"]
    fixture = isolated_repository / relative
    assert fixture.resolve().is_relative_to(isolated_repository.resolve())
    assert fixture.is_file(), relative
    fixture.unlink()

    result = _validate(isolated_repository)

    assert result.returncode != 0, result.stdout + result.stderr
    assert f"PYQ107: missing or outside target path: {relative}" in result.stderr


def test_host_stdout_without_gate_required_check_fails(isolated_repository: Path) -> None:
    """Even a successful query cannot substitute other checks for the required gate."""
    row = _row(isolated_repository, "GIT.required-checks")
    assert row["status"] == "implemented", "required gate needs live implemented host evidence"
    evidence = row["host_query"]
    assert any("gate" in token for token in evidence["contains"])
    record_path = isolated_repository / evidence["path"]
    assert record_path.resolve().is_relative_to(isolated_repository.resolve())
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert "gate" in record["stdout"], "baseline live host record must contain required gate"
    # Leave gate in stderr to prove implemented evidence uses stdout only.
    record["stderr"] += record["stdout"]
    record["stdout"] = record["stdout"].replace("gate", "unrequired-check")
    record_path.write_text(json.dumps(record), encoding="utf-8")

    result = _validate(isolated_repository)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "GIT.required-checks: host query assertion failed" in result.stderr


def test_hook_installation_requires_installed_manager_in_isolated_clone(
    tmp_path: Path, isolated_repository: Path
) -> None:
    """Inspect absent/present real manager wiring and prove failure propagation locally."""
    clone = tmp_path / "clone"
    cloned = _run(["git", "clone", "--local", "--no-hardlinks", str(ROOT), str(clone)], tmp_path)
    assert cloned.returncode == 0, cloned.stderr
    # Exercise current definitions, including not-yet-committed implementation repairs.
    shutil.copytree(isolated_repository, clone, dirs_exist_ok=True)
    for key, value in (
        ("user.name", "Rule Evidence Canary"),
        ("user.email", "rule-evidence@example.invalid"),
        ("commit.gpgsign", "false"),
    ):
        configured = _run(["git", "config", key, value], clone)
        assert configured.returncode == 0, configured.stderr
    checker = clone / HOOK_CHECK
    assert checker.is_file(), f"missing real hook installation checker: {HOOK_CHECK}"
    command = [sys.executable, str(checker), "--root", str(clone)]
    diagnostic = _row(clone, "GATE.pre-commit")["hook_install_check"]["diagnostic"]
    hook = clone / ".git/hooks/pre-commit"
    hook.unlink(missing_ok=True)

    absent = _run(command, clone)
    assert absent.returncode != 0, absent.stdout + absent.stderr
    assert diagnostic not in absent.stdout

    installed = _run(
        [sys.executable, "-m", "pre_commit", "install", "--hook-type", "pre-commit"], clone
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert hook.is_file()
    present = _run(command, clone)
    assert present.returncode == 0, present.stdout + present.stderr
    assert diagnostic in present.stdout + present.stderr
