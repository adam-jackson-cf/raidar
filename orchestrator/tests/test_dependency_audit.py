"""Fast behavioural proofs for the whole-repository dependency audit."""

from __future__ import annotations

import importlib.util
import json
import math
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
RULE = "SECURITY.dependency-audit"
EXCEPTIONS = "scripts/checks/dependency-audit-exceptions.json"
TODAY = date(2026, 6, 1)
UV_LOCK = """\
version = 1
revision = 3
[[package]]
name = "app"
version = "0"
source = { editable = "." }
[[package]]
name = "lib"
version = "0"
source = { editable = "../lib" }
[[package]]
name = "widget"
version = "1.5.0"
source = { registry = "https://pypi.org/simple" }
"""
LOCAL_ONLY_LOCK = """\
version = 1
revision = 3
[[package]]
name = "tool"
version = "0"
source = { virtual = "." }
"""
NPM_LOCK = json.dumps(
    {
        "lockfileVersion": 3,
        "packages": {
            "": {"workspaces": ["packages/ui"]},
            "packages/ui": {"dependencies": {"left-pad": "^1.0.0"}},
            "node_modules/ui": {"link": True, "resolved": "packages/ui"},
            "node_modules/left-pad": {"version": "1.0.0"},
        },
    }
)
FILES = {
    "app/pyproject.toml": '[project]\nname = "app"\ndependencies = ["widget", "lib"]\n',
    "app/uv.lock": UV_LOCK,
    "lib/pyproject.toml": '[project]\nname = "lib"\ndependencies = ["widget"]\n',
    "tool/pyproject.toml": '[project]\nname = "tool"\ndependencies = []\n',
    "tool/uv.lock": LOCAL_ONLY_LOCK,
    "web/package.json": json.dumps({"workspaces": ["packages/ui"]}),
    "web/package-lock.json": NPM_LOCK,
    "web/packages/ui/package.json": json.dumps({"dependencies": {"left-pad": "^1.0.0"}}),
    "rust/Cargo.toml": '[package]\nname = "r"\n[dependencies]\nserde = "1"\n',
    "rust/Cargo.lock": "version = 3\n",
    "rust/package.json": json.dumps({"devDependencies": {"prettier": "3.6.2"}}),
    "loose/pyproject.toml": '[project]\nname = "loose"\ndependencies = ["requests"]\n',
    "empty/package.json": json.dumps({"name": "empty", "private": True}),
    "lab/stages/acme/probe/fixtures/bad/uv.lock": "not a lock",
    "lab/stages/acme/probe/fixtures/bad/package.json": json.dumps({"dependencies": {"x": "1"}}),
    "lab/stages/acme/probe/fixtures/bad/yarn.lock": "",
    EXCEPTIONS: json.dumps({"schemaVersion": 1, "exceptions": []}),
}
LAYOUT_FINDINGS = [
    f"{RULE} failure=manifest-without-lock path=loose/pyproject.toml reason=dependencies are "
    "declared with no lock of their ecosystem: commit a lock file beside the manifest or pin "
    "it from a workspace lock",
    f"{RULE} failure=manifest-without-lock path=rust/package.json reason=dependencies are "
    "declared with no lock of their ecosystem: commit a lock file beside the manifest or pin "
    "it from a workspace lock",
    f"{RULE} failure=unknown-lock-format path=rust/Cargo.lock reason=no auditor for Cargo.lock: "
    "add one",
]


def pip_report(*vulns: Mapping[str, object]) -> str:
    return json.dumps(
        {"dependencies": [{"name": "widget", "version": "1.5.0", "vulns": list(vulns)}]}
    )


def npm_report(*vias: object) -> str:
    vulnerabilities = {
        "left-pad": {"name": "left-pad", "via": list(vias), "nodes": ["node_modules/left-pad"]}
    }
    if not vias:
        vulnerabilities = {}
    return json.dumps({"auditReportVersion": 2, "vulnerabilities": vulnerabilities, "metadata": {}})


PIP_VULN = {
    "id": "PYSEC-2026-1",
    "fix_versions": ["1.5.1", "2.0.0"],
    "aliases": ["GHSA-aaaa-bbbb-cccc", "CVE-2026-0001"],
}
NPM_VIA = {
    "source": 1,
    "name": "left-pad",
    "url": "https://github.com/advisories/GHSA-2222-3333-4444",
    "range": "<=1.0.2",
}


class AuditRunner(Protocol):
    def __call__(
        self, command: list[str], *, stdin: str | None = None, timeout: float
    ) -> subprocess.CompletedProcess[str]: ...


class AuditChecker(Protocol):
    ADVISORY_LINE: re.Pattern[str]
    FAILURE_LINE: re.Pattern[str]

    def audit_findings(
        self, root: Path, paths: Sequence[str], *, run: AuditRunner, today: date
    ) -> list[str]: ...

    def malicious_aliases(
        self, ident: str, aliases: Sequence[str], marked: bool
    ) -> tuple[str, ...]: ...

    def npm_fixes(
        self, installed: str, vulnerable: str, published: Sequence[str]
    ) -> tuple[str, ...]: ...


@pytest.fixture
def checker() -> AuditChecker:
    spec = importlib.util.spec_from_file_location(
        "dependency_audit_proofs", ROOT / "scripts/checks/check-toolchain-parity.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("AuditChecker", module)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for path, text in FILES.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text)
    return tmp_path


@dataclass
class ScriptedAuditors:
    pip: str = pip_report()
    pip_returncode: int = 0
    npm: str = npm_report()
    npm_returncode: int = 0
    published: str = json.dumps(["1.0.0", "1.0.2", "1.0.3-beta.1", "1.0.3", "2.0.0"])
    failure: str = ""
    calls: list[tuple[list[str], str | None]] = field(default_factory=list)

    def __call__(
        self, command: list[str], *, stdin: str | None = None, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, stdin))
        assert math.isfinite(timeout)
        assert timeout > 0
        if self.failure == "timeout":
            raise subprocess.TimeoutExpired(command, timeout)
        if self.failure == "launch":
            raise OSError("audit tool unavailable")
        if command[:2] == ["uv", "export"]:
            empty = self.failure == "export-empty"
            output = "" if empty else "widget==1.5.0 --hash=sha256:fixture\n"
            return subprocess.CompletedProcess(
                command, int(self.failure == "export-nonzero"), output, ""
            )
        if "pip-audit" in command:
            return subprocess.CompletedProcess(command, self.pip_returncode, self.pip, "")
        if command[:2] == ["npm", "audit"]:
            return subprocess.CompletedProcess(command, self.npm_returncode, self.npm, "")
        if command[:2] == ["npm", "view"]:
            return subprocess.CompletedProcess(command, 0, self.published, "")
        raise AssertionError(f"unexpected command: {command}")


def audit(checker: AuditChecker, repo: Path, runner: ScriptedAuditors) -> list[str]:
    """Findings beyond the fixed layout ones, which the discovery proof asserts exactly."""
    paths = sorted(path for path in FILES if (repo / path).exists())
    found = checker.audit_findings(repo, paths, run=runner, today=TODAY)
    assert set(LAYOUT_FINDINGS) <= set(found)
    return [line for line in found if line not in LAYOUT_FINDINGS]


def test_audit_discovers_every_lock_and_manifest_outside_fixture_programs(
    checker: AuditChecker, repo: Path
) -> None:
    runner = ScriptedAuditors()
    paths = sorted(FILES)
    assert checker.audit_findings(repo, paths, run=runner, today=TODAY) == LAYOUT_FINDINGS
    commands = [command for command, _ in runner.calls]
    assert commands == [
        [
            "uv",
            "export",
            "--project",
            "app",
            "--locked",
            "--all-extras",
            "--all-groups",
            "--all-packages",
            "--format",
            "requirements-txt",
            "--no-emit-local",
        ],
        [
            "uv",
            "run",
            "--project",
            "orchestrator",
            "--extra",
            "dev",
            "--locked",
            "pip-audit",
            "--strict",
            "--disable-pip",
            "--no-deps",
            "--format",
            "json",
            "--requirement",
            "/dev/stdin",
        ],
        [
            "npm",
            "audit",
            "--json",
            "--package-lock-only",
            "--include=dev",
            "--include=optional",
            "--include=peer",
            "--prefix",
            "web",
        ],
    ]
    assert runner.calls[1][1] == "widget==1.5.0 --hash=sha256:fixture\n"
    assert not any("fixtures" in " ".join(command) for command in commands)


def test_requirements_files_are_audited_directly(checker: AuditChecker, repo: Path) -> None:
    (repo / "loose/requirements-dev.txt").write_text("widget==1.5.0\n")
    runner = ScriptedAuditors()
    found = checker.audit_findings(
        repo,
        ["loose/pyproject.toml", "loose/requirements-dev.txt", EXCEPTIONS],
        run=runner,
        today=TODAY,
    )
    assert found == []
    assert runner.calls == [
        (
            [
                "uv",
                "run",
                "--project",
                "orchestrator",
                "--extra",
                "dev",
                "--locked",
                "pip-audit",
                "--strict",
                "--disable-pip",
                "--no-deps",
                "--format",
                "json",
                "--requirement",
                "/dev/stdin",
            ],
            "widget==1.5.0\n",
        )
    ]


def test_every_advisory_blocks_with_a_machine_readable_line(
    checker: AuditChecker, repo: Path
) -> None:
    unfixed = {"id": "PYSEC-2026-2", "fix_versions": []}
    runner = ScriptedAuditors(
        pip=pip_report(PIP_VULN, unfixed),
        pip_returncode=1,
        npm=npm_report(NPM_VIA),
        npm_returncode=1,
    )
    found = audit(checker, repo, runner)
    assert found == [
        f"{RULE} lock=app/uv.lock ecosystem=PyPI package=widget version=1.5.0 "
        "advisory=PYSEC-2026-1 aliases=CVE-2026-0001,GHSA-aaaa-bbbb-cccc fix=1.5.1,2.0.0",
        f"{RULE} lock=app/uv.lock ecosystem=PyPI package=widget version=1.5.0 "
        "advisory=PYSEC-2026-2 aliases= fix=",
        f"{RULE} lock=web/package-lock.json ecosystem=npm package=left-pad version=1.0.0 "
        "advisory=GHSA-2222-3333-4444 aliases= fix=1.0.3",
    ]
    parsed = [checker.ADVISORY_LINE.fullmatch(line) for line in found]
    assert all(parsed)
    first = parsed[0]
    assert first is not None
    assert first.group("lock", "ecosystem", "package", "version", "advisory") == (
        "app/uv.lock",
        "PyPI",
        "widget",
        "1.5.0",
        "PYSEC-2026-1",
    )
    assert first.group("aliases").split(",") == ["CVE-2026-0001", "GHSA-aaaa-bbbb-cccc"]
    assert first.group("fix").split(",") == ["1.5.1", "2.0.0"]
    assert all(checker.FAILURE_LINE.fullmatch(line) for line in LAYOUT_FINDINGS)
    assert not any(checker.ADVISORY_LINE.fullmatch(line) for line in LAYOUT_FINDINGS)
    assert not any(checker.FAILURE_LINE.fullmatch(line) for line in found)


@pytest.mark.parametrize(
    ("mark", "aliases"),
    [
        ({"cwe": ["CWE-506"], "title": "Embedded code"}, "MAL-GHSA-2222-3333-4444"),
        ({"cwe": [], "title": "Malicious code in left-pad"}, "MAL-GHSA-2222-3333-4444"),
        ({"cwe": ["CWE-79"], "title": "Cross-site scripting"}, ""),
    ],
)
def test_auditor_malicious_mark_adds_a_mal_alias(
    checker: AuditChecker, repo: Path, mark: Mapping[str, object], aliases: str
) -> None:
    runner = ScriptedAuditors(npm=npm_report({**NPM_VIA, **mark}), npm_returncode=1)
    assert audit(checker, repo, runner) == [
        f"{RULE} lock=web/package-lock.json ecosystem=npm package=left-pad version=1.0.0 "
        f"advisory=GHSA-2222-3333-4444 aliases={aliases} fix=1.0.3"
    ]


def test_existing_mal_record_is_not_duplicated(checker: AuditChecker, repo: Path) -> None:
    vuln = {"id": "MAL-2026-7", "fix_versions": [], "aliases": ["GHSA-aaaa-bbbb-cccc"]}
    runner = ScriptedAuditors(pip=pip_report(vuln), pip_returncode=1)
    assert audit(checker, repo, runner) == [
        f"{RULE} lock=app/uv.lock ecosystem=PyPI package=widget version=1.5.0 "
        "advisory=MAL-2026-7 aliases=GHSA-aaaa-bbbb-cccc fix="
    ]
    assert checker.malicious_aliases("GHSA-x", ["MAL-2026-1"], True) == ("MAL-2026-1",)
    assert checker.malicious_aliases("GHSA-x", [], True) == ("MAL-GHSA-x",)
    assert checker.malicious_aliases("GHSA-x", [], False) == ()


@pytest.mark.parametrize(
    ("installed", "vulnerable", "published", "expected"),
    [
        ("4.28.2", "<=4.28.6", ["4.28.6", "4.28.7-beta.1", "4.28.7", "5.0.0"], ("4.28.7",)),
        ("6.1.0", ">=6.0.0 <7.18.0", ["6.2.0", "7.17.9", "7.18.0"], ("7.18.0",)),
        ("1.2.0", ">=1.0.0 <1.2.3 || >=2.0.0 <2.0.5", ["1.2.3", "2.0.0"], ("1.2.3",)),
        ("1.0.0", ">= 1.0.0", ["1.0.1", "2.0.0"], ()),
        ("1.0.0", "*", ["1.0.1"], ()),
    ],
)
def test_npm_fix_is_the_lowest_release_above_the_lock_outside_the_range(
    checker: AuditChecker,
    installed: str,
    vulnerable: str,
    published: list[str],
    expected: tuple[str, ...],
) -> None:
    assert checker.npm_fixes(installed, vulnerable, published) == expected


PIP_FAILURES = [
    ScriptedAuditors(pip=pip_report(PIP_VULN), pip_returncode=2),
    ScriptedAuditors(pip_returncode=1),
    ScriptedAuditors(pip=json.dumps({"dependencies": [{"name": "widget", "skip_reason": "x"}]})),
    ScriptedAuditors(pip=pip_report({"id": "PYSEC-1", "fix_versions": [], "aliases": "bad"})),
    ScriptedAuditors(pip="not-json"),
    ScriptedAuditors(pip="{}"),
    ScriptedAuditors(pip=""),
    ScriptedAuditors(failure="export-nonzero"),
    ScriptedAuditors(failure="export-empty"),
]
NPM_FAILURES = [
    ScriptedAuditors(npm_returncode=1),
    ScriptedAuditors(npm=json.dumps({"error": {"code": "ENOLOCK"}})),
    ScriptedAuditors(npm=npm_report({**NPM_VIA, "url": "https://example.test/advisory"})),
    ScriptedAuditors(npm=npm_report(NPM_VIA), published="not-json"),
    ScriptedAuditors(npm="not-json"),
]


@pytest.mark.parametrize(
    ("lock", "runner"),
    [
        *(("app/uv.lock", runner) for runner in PIP_FAILURES),
        *(("web/package-lock.json", runner) for runner in NPM_FAILURES),
    ],
)
def test_audit_fails_closed_on_unusable_report_or_exit(
    checker: AuditChecker, repo: Path, lock: str, runner: ScriptedAuditors
) -> None:
    found = audit(checker, repo, runner)
    assert len(found) == 1
    assert re.fullmatch(
        rf"{re.escape(RULE)} failure=auditor path={re.escape(lock)} reason=\S.*", found[0]
    )
    assert checker.FAILURE_LINE.fullmatch(found[0])


@pytest.mark.parametrize(
    ("failure", "error"), [("timeout", "TimeoutExpired"), ("launch", "OSError")]
)
def test_audit_tool_failure_is_a_finding_for_each_audited_lock(
    checker: AuditChecker, repo: Path, failure: str, error: str
) -> None:
    found = audit(checker, repo, ScriptedAuditors(failure=failure))
    assert found == [
        f"{RULE} failure=auditor path={lock} reason=audit tool failed ({error})"
        for lock in ("app/uv.lock", "web/package-lock.json")
    ]


VALID_EXCEPTION = {
    "advisory": "PYSEC-2026-2",
    "package": "widget",
    "version": "1.5.0",
    "lock": "app/uv.lock",
    "reason": "the only maintained widget implementation",
    "exposure": "only parses repository-owned files",
    "expires": "2026-06-01",
}
UNFIXED = {"id": "PYSEC-2026-2", "fix_versions": [], "aliases": ["CVE-2026-0002"]}


def audit_with_exceptions(
    checker: AuditChecker, repo: Path, exceptions: object, vuln: Mapping[str, object] = UNFIXED
) -> list[str]:
    (repo / EXCEPTIONS).write_text(json.dumps(exceptions))
    runner = ScriptedAuditors(pip=pip_report(vuln), pip_returncode=1)
    return audit(checker, repo, runner)


def test_valid_exception_suppresses_only_its_unfixed_advisory(
    checker: AuditChecker, repo: Path
) -> None:
    document = {"schemaVersion": 1, "exceptions": [VALID_EXCEPTION]}
    assert audit_with_exceptions(checker, repo, document) == []
    by_alias = {**VALID_EXCEPTION, "advisory": "CVE-2026-0002"}
    assert (
        audit_with_exceptions(checker, repo, {"schemaVersion": 1, "exceptions": [by_alias]}) == []
    )
    other = {"id": "PYSEC-2026-3", "fix_versions": []}
    found = audit_with_exceptions(checker, repo, document, other)
    assert [line.split("advisory=")[1].split(" ")[0] for line in found[:1]] == ["PYSEC-2026-3"]
    assert found[1:] == [
        f"{RULE} failure=exception path={EXCEPTIONS} reason=exception 0 PYSEC-2026-2 "
        "matches no current finding"
    ]


@pytest.mark.parametrize(
    ("change", "vuln", "reason"),
    [
        ({"reason": None}, UNFIXED, "fields differ: missing ['reason'] extra none"),
        ({"severity": "low"}, UNFIXED, "fields differ: missing none extra ['severity']"),
        ({"exposure": " "}, UNFIXED, "fields must be non-empty strings"),
        ({"expires": "soon"}, UNFIXED, "expires soon is not an ISO date"),
        ({"expires": "2026-05-31"}, UNFIXED, "expired on 2026-05-31"),
        ({"version": "1.4.0"}, UNFIXED, "version widget==1.4.0 no longer matches app/uv.lock"),
        ({"lock": "other/uv.lock"}, UNFIXED, "names other/uv.lock, which has no completed audit"),
        ({}, {**UNFIXED, "fix_versions": ["1.6.0"]}, "PYSEC-2026-2 now has a fix (1.6.0)"),
        ({"advisory": "PYSEC-2026-9"}, UNFIXED, "PYSEC-2026-9 matches no current finding"),
    ],
)
def test_invalid_exception_is_a_finding_and_suppresses_nothing(
    checker: AuditChecker,
    repo: Path,
    change: Mapping[str, object],
    vuln: Mapping[str, object],
    reason: str,
) -> None:
    entry = {**VALID_EXCEPTION, **change}
    entry = {key: value for key, value in entry.items() if value is not None}
    found = audit_with_exceptions(checker, repo, {"schemaVersion": 1, "exceptions": [entry]}, vuln)
    assert len(found) == 2
    assert checker.ADVISORY_LINE.fullmatch(found[0])
    assert found[1] == f"{RULE} failure=exception path={EXCEPTIONS} reason=exception 0 {reason}"


def test_duplicate_exception_is_a_finding(checker: AuditChecker, repo: Path) -> None:
    document = {"schemaVersion": 1, "exceptions": [VALID_EXCEPTION, VALID_EXCEPTION]}
    assert audit_with_exceptions(checker, repo, document) == [
        f"{RULE} failure=exception path={EXCEPTIONS} reason=exception 1 PYSEC-2026-2 "
        "duplicates another exception"
    ]


@pytest.mark.parametrize(
    "document",
    [
        None,
        {"exceptions": []},
        {"schemaVersion": 2, "exceptions": []},
        {"schemaVersion": 1, "exceptions": {}},
    ],
)
def test_malformed_or_missing_exceptions_file_is_a_finding(
    checker: AuditChecker, repo: Path, document: object
) -> None:
    if document is None:
        (repo / EXCEPTIONS).unlink()
    else:
        (repo / EXCEPTIONS).write_text(json.dumps(document))
    found = audit(checker, repo, ScriptedAuditors())
    assert len(found) == 1
    assert found[0].startswith(
        f"{RULE} failure=exception path={EXCEPTIONS} reason=exceptions file "
    )
    assert checker.FAILURE_LINE.fullmatch(found[0])
