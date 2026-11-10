"""Fast behavioural proofs for toolchain parity and strict dependency auditing."""

from __future__ import annotations

import importlib.util
import json
import math
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = """\
[project]
name = "raidar"
version = "1.0.1"
dependencies = ["widget>=1.0,<2", "raidar-contract==2.0.0"]
[project.optional-dependencies]
dev = ["pip-audit==2.9.0", "pytest>=9"]
analysis = ["networkx==3.6.1"]
"""
LOCK = """\
version = 1
revision = 3
[[package]]
name = "raidar"
version = "1.0.1"
source = { editable = "." }
[[package]]
name = "raidar-contract"
version = "2.0.0"
source = { editable = "../contract" }
[[package]]
name = "widget"
version = "1.5.0"
[[package]]
name = "pip-audit"
version = "2.9.0"
[[package]]
name = "pytest"
version = "9.0.3"
[[package]]
name = "networkx"
version = "3.6.1"
"""
SYNC = "env -u AGENT uv sync --project orchestrator --all-extras --locked"
AUDIT_RULE = "SECURITY.dependency-audit"
LOCK_PATH = "orchestrator/uv.lock"


class AuditRunner(Protocol):
    def __call__(
        self, command: list[str], *, stdin: str | None = None, timeout: float
    ) -> subprocess.CompletedProcess[str]: ...


class ParityChecker(Protocol):
    def dependency_inventory_findings(
        self, project_toml: str, install_commands: Mapping[str, str]
    ) -> list[str]: ...

    def install_guidance_findings(
        self,
        guidance: Mapping[str, str],
        install_commands: Mapping[str, str],
        make_targets: Mapping[str, str],
    ) -> list[str]: ...

    def lock_version_findings(self, projects: Mapping[str, str], lock_toml: str) -> list[str]: ...

    def prettier_version_findings(self, sources: Mapping[str, str]) -> list[str]: ...

    def prettier_paths(self, tracked_paths: Sequence[str]) -> list[str]: ...

    def audit_pin_findings(self, project_toml: str, lock_toml: str) -> list[str]: ...

    def audit_findings(
        self, changed_paths: Sequence[str], *, ci: bool, run: AuditRunner
    ) -> list[str]: ...


@pytest.fixture
def checker() -> ParityChecker:
    spec = importlib.util.spec_from_file_location(
        "toolchain_parity_proofs", ROOT / "scripts/checks/check-toolchain-parity.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("ParityChecker", module)


def _assert_finding(found: list[str], path: str, rule: str) -> None:
    assert len(found) == 1
    assert re.fullmatch(rf"{re.escape(path)}: {re.escape(rule)}: [^\r\n;]+; [^\r\n]+", found[0])


@pytest.mark.parametrize(
    "command",
    [
        SYNC,
        "uv sync --locked --extra dev --project orchestrator --extra=analysis",
        "uv sync --project orchestrator --all-extras --frozen --python 3.13.7",
    ],
)
def test_dependency_inventory_accepts_all_declared_extras(
    checker: ParityChecker, command: str
) -> None:
    assert checker.dependency_inventory_findings(PROJECT, {"Makefile": command}) == []


@pytest.mark.parametrize(
    "command",
    [
        "uv sync --project orchestrator --locked",
        "uv sync --project orchestrator --extra dev --locked",
        "uv sync --project orchestrator --all-extras --no-extra analysis --locked",
        "uv sync --project orchestrator --all-extras --extra typo --locked",
    ],
)
def test_dependency_inventory_refuses_omitted_or_unknown_extras(
    checker: ParityChecker, command: str
) -> None:
    found = checker.dependency_inventory_findings(PROJECT, {"Makefile": command})
    _assert_finding(found, "Makefile", "PARITY.dependency-inventory")


def test_dependency_inventory_checks_each_installer_not_their_union(checker: ParityChecker) -> None:
    found = checker.dependency_inventory_findings(
        PROJECT,
        {"Makefile": SYNC, ".github/workflows/gate.yml": "uv sync --extra dev --locked"},
    )
    _assert_finding(found, ".github/workflows/gate.yml", "PARITY.dependency-inventory")


@pytest.mark.parametrize(
    "documented",
    [
        "env -u AGENT uv sync --frozen --all-extras --project orchestrator",
        "env -u AGENT make env-setup",
    ],
)
def test_install_guidance_accepts_equivalent_resolution_and_public_make_target(
    checker: ParityChecker, documented: str
) -> None:
    assert (
        checker.install_guidance_findings(
            {"README.md": f"# Install\n```sh\n{documented}\n```\n"},
            {"Makefile": SYNC},
            {"env-setup": SYNC},
        )
        == []
    )


@pytest.mark.parametrize(
    "documented",
    [
        "uv sync --project orchestrator --all-extras",
        "uv sync --project contract --all-extras --locked",
        "uv sync --project orchestrator --extra dev --locked",
        "make stale-setup",
    ],
)
def test_install_guidance_refuses_drift(checker: ParityChecker, documented: str) -> None:
    found = checker.install_guidance_findings(
        {"README.md": f"## Install\n```bash\n{documented}\n```\n"},
        {"Makefile": SYNC},
        {"env-setup": SYNC},
    )
    _assert_finding(found, "README.md", "PARITY.install-guidance")


def test_install_guidance_ignores_prose_and_unrelated_commands(checker: ParityChecker) -> None:
    assert (
        checker.install_guidance_findings(
            {"README.md": "Mention uv sync in prose.\n```sh\nmake test-fast\n```\n"},
            {"Makefile": SYNC},
            {"env-setup": SYNC},
        )
        == []
    )


def _projects() -> dict[str, str]:
    return {
        "orchestrator/pyproject.toml": PROJECT,
        "contract/pyproject.toml": '[project]\nname = "raidar-contract"\nversion = "2.0.0"\n',
    }


def test_lock_versions_accept_ranges_extras_and_normalized_names(checker: ParityChecker) -> None:
    assert (
        checker.lock_version_findings(
            _projects(), LOCK.replace('name = "pip-audit"', 'name = "pip_audit"')
        )
        == []
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            'name = "raidar-contract"\nversion = "2.0.0"',
            'name = "raidar-contract"\nversion = "1.0.0"',
        ),
        ('name = "networkx"\nversion = "3.6.1"', 'name = "networkx"\nversion = "3.5.0"'),
        ('name = "widget"\nversion = "1.5.0"', 'name = "widget"\nversion = "2.0.0"'),
        ('name = "pytest"', 'name = "not-pytest"'),
    ],
)
def test_lock_versions_refuse_non_raidar_drift(checker: ParityChecker, old: str, new: str) -> None:
    _assert_finding(
        checker.lock_version_findings(_projects(), LOCK.replace(old, new)),
        LOCK_PATH,
        "PARITY.lock-version",
    )


def test_lock_versions_compare_local_metadata_even_without_dependency_pin(
    checker: ParityChecker,
) -> None:
    projects = _projects()
    projects["contract/pyproject.toml"] = projects["contract/pyproject.toml"].replace(
        "2.0.0", "2.1.0"
    )
    _assert_finding(checker.lock_version_findings(projects, LOCK), LOCK_PATH, "PARITY.lock-version")


def _prettier_sources() -> dict[str, str]:
    return {
        "package.json": json.dumps({"devDependencies": {"prettier": "3.6.2"}}),
        "package-lock.json": json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"devDependencies": {"prettier": "3.6.2"}},
                    "node_modules/prettier": {
                        "version": "3.6.2",
                        "resolved": "https://registry.npmjs.org/prettier/-/prettier-3.6.2.tgz",
                        "integrity": "sha512-fixture",
                    },
                },
            }
        ),
        ".github/workflows/format.yml": "run: npm exec --no -- prettier --check README.md\n",
    }


def test_prettier_accepts_exact_locked_toolchain(checker: ParityChecker) -> None:
    assert checker.prettier_version_findings(_prettier_sources()) == []


@pytest.mark.parametrize("version", ["^3.6.2", "latest", "3.7.0"])
def test_prettier_refuses_unpinned_or_wrong_declared_version(
    checker: ParityChecker, version: str
) -> None:
    sources = _prettier_sources()
    sources["package.json"] = json.dumps({"devDependencies": {"prettier": version}})
    _assert_finding(checker.prettier_version_findings(sources), "package.json", "PRETTIER.version")


@pytest.mark.parametrize("lock_state", ["missing", "wrong-version"])
def test_prettier_requires_matching_lock(checker: ParityChecker, lock_state: str) -> None:
    sources = _prettier_sources()
    if lock_state == "missing":
        del sources["package-lock.json"]
    else:
        sources["package-lock.json"] = sources["package-lock.json"].replace("3.6.2", "3.7.0")
    _assert_finding(
        checker.prettier_version_findings(sources), "package-lock.json", "PRETTIER.version"
    )


@pytest.mark.parametrize(
    "command", ["npx --yes prettier --check README.md", "npx prettier@latest --check README.md"]
)
def test_prettier_refuses_network_floating_invocation(checker: ParityChecker, command: str) -> None:
    sources = _prettier_sources()
    sources[".github/workflows/format.yml"] = f"run: {command}\n"
    _assert_finding(
        checker.prettier_version_findings(sources),
        ".github/workflows/format.yml",
        "PRETTIER.version",
    )


def test_prettier_selects_maintained_markdown_yaml_without_protected_docs(
    checker: ParityChecker,
) -> None:
    assert checker.prettier_paths(
        [
            "README.md",
            ".github/workflows/gate.yml",
            ".agents/skills/test-patterns/SKILL.md",
            "lab/workflows/example.yaml",
            "docs/private.md",
            "docs/reboot/handoff.md",
            "var/report.yaml",
            "node_modules/prettier/README.md",
            "review-surface/node_modules/pkg/config.yml",
            "orchestrator/.venv/lib/README.md",
            "review-surface/dist/report.yaml",
            "orchestrator/src/example.py",
        ]
    ) == [
        ".agents/skills/test-patterns/SKILL.md",
        ".github/workflows/gate.yml",
        "README.md",
        "lab/workflows/example.yaml",
    ]


def test_dependency_audit_accepts_exact_dev_pin_and_matching_lock(checker: ParityChecker) -> None:
    assert checker.audit_pin_findings(PROJECT, LOCK) == []


@pytest.mark.parametrize("declaration", ["pip-audit>=2.9.0", "other-tool==2.9.0"])
def test_dependency_audit_requires_exact_dev_pin(checker: ParityChecker, declaration: str) -> None:
    _assert_finding(
        checker.audit_pin_findings(PROJECT.replace("pip-audit==2.9.0", declaration), LOCK),
        "orchestrator/pyproject.toml",
        AUDIT_RULE,
    )


def test_dependency_audit_refuses_pin_lock_drift(checker: ParityChecker) -> None:
    _assert_finding(
        checker.audit_pin_findings(PROJECT, LOCK.replace('version = "2.9.0"', 'version = "2.8.0"')),
        LOCK_PATH,
        AUDIT_RULE,
    )


CLEAN_REPORT = json.dumps(
    {"dependencies": [{"name": "widget", "version": "1.5.0", "vulns": []}], "fixes": []}
)
EXPORTED = "widget==1.5.0 --hash=sha256:fixture\n"


@dataclass
class ScriptedAudit:
    report: str = CLEAN_REPORT
    audit_returncode: int = 0
    failure: str = ""
    calls: list[tuple[list[str], str | None, float]] = field(default_factory=list)

    def __call__(
        self, command: list[str], *, stdin: str | None = None, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, stdin, timeout))
        assert math.isfinite(timeout)
        assert timeout > 0
        exporting = "export" in command
        if self.failure == "export-nonzero" and exporting:
            return subprocess.CompletedProcess(command, 2, "", "export failed")
        if self.failure == "export-empty" and exporting:
            return subprocess.CompletedProcess(command, 0, "", "")
        if exporting:
            return subprocess.CompletedProcess(command, 0, EXPORTED, "")
        if self.failure == "timeout":
            raise subprocess.TimeoutExpired(command, timeout)
        if self.failure == "launch":
            raise OSError("audit tool unavailable")
        return subprocess.CompletedProcess(command, self.audit_returncode, self.report, "")


def _never_run(
    command: list[str], *, stdin: str | None = None, timeout: float
) -> subprocess.CompletedProcess[str]:
    raise AssertionError(f"Unrelated commit must not run audit: {command}")


def test_dependency_audit_skips_unrelated_commit_changes(checker: ParityChecker) -> None:
    assert (
        checker.audit_findings(
            ["README.md", "orchestrator/src/raidar/cli/main.py"], ci=False, run=_never_run
        )
        == []
    )


@pytest.mark.parametrize(
    "changed",
    [
        "orchestrator/pyproject.toml",
        "contract/pyproject.toml",
        "orchestrator/uv.lock",
        "review-surface/package.json",
        "review-surface/package-lock.json",
        "requirements-dev.txt",
    ],
)
def test_dependency_audit_runs_for_dependency_changes_without_reading_changed_file(
    checker: ParityChecker, changed: str
) -> None:
    runner = ScriptedAudit()
    assert checker.audit_findings([changed], ci=False, run=runner) == []
    assert len(runner.calls) == 2


def test_dependency_audit_ci_always_audits_exported_locked_requirements(
    checker: ParityChecker,
) -> None:
    runner = ScriptedAudit()
    assert checker.audit_findings([], ci=True, run=runner) == []
    assert len(runner.calls) == 2
    export, audit = runner.calls
    assert "export" in export[0]
    assert "orchestrator" in export[0]
    assert "--locked" in export[0]
    assert "--all-extras" in export[0]
    assert "pip-audit" in audit[0]
    assert "--strict" in audit[0]
    assert "--locked" in audit[0]
    assert "--format" in audit[0]
    assert "json" in audit[0]
    assert audit[1] == EXPORTED
    assert "--ignore-vuln" not in audit[0]


@pytest.mark.parametrize(
    "report",
    [
        json.dumps(
            {
                "dependencies": [
                    {
                        "name": "widget",
                        "version": "1.5.0",
                        "vulns": [{"id": "CVE-fixture", "fix_versions": ["1.5.1"]}],
                    }
                ],
                "fixes": [],
            }
        ),
        json.dumps(
            {"dependencies": [{"name": "widget", "skip_reason": "not resolved"}], "fixes": []}
        ),
        "not-json",
        "{}",
        "null",
        "",
    ],
)
def test_dependency_audit_refuses_findings_or_unusable_report_even_with_zero_exit(
    checker: ParityChecker, report: str
) -> None:
    _assert_finding(
        checker.audit_findings([], ci=True, run=ScriptedAudit(report=report)),
        LOCK_PATH,
        AUDIT_RULE,
    )


def test_dependency_audit_nonzero_exit_cannot_be_hidden_by_clean_report(
    checker: ParityChecker,
) -> None:
    _assert_finding(
        checker.audit_findings([], ci=True, run=ScriptedAudit(audit_returncode=2)),
        LOCK_PATH,
        AUDIT_RULE,
    )


@pytest.mark.parametrize("failure", ["timeout", "launch", "export-nonzero", "export-empty"])
def test_dependency_audit_fails_closed_on_tool_or_export_failure(
    checker: ParityChecker, failure: str
) -> None:
    runner = ScriptedAudit(failure=failure)
    _assert_finding(checker.audit_findings([], ci=True, run=runner), LOCK_PATH, AUDIT_RULE)
    if failure.startswith("export-"):
        assert len(runner.calls) == 1
