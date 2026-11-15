#!/usr/bin/env python3
"""Check maintained installer, lock, formatter and dependency-audit parity.

``audit`` mode audits the whole repository on every run. It discovers dependency
manifests and lock files from the git inventory, excluding only fixture programs, and
fails closed:

- ``uv.lock`` is exported (``uv export --project <its directory> --locked``) and audited
  by the pinned pip-audit; ``requirements*.txt`` goes to pip-audit directly;
  ``package-lock.json``/``npm-shrinkwrap.json`` go to ``npm audit --package-lock-only``.
- Any other recognised lock format is an ``unknown-lock-format`` failure.
- A manifest that declares dependencies with no lock of its ecosystem beside it, and
  that no lock pins as a uv path source or npm workspace, is a
  ``manifest-without-lock`` failure.
- Every advisory blocks, fixable or not. Only a valid owner-approved entry in
  ``scripts/checks/dependency-audit-exceptions.json`` suppresses one advisory, and only
  while no fixed release exists.

Every audit line starts with the rule ID and takes one of two forms, fields in exactly
this order and separated by single spaces (``ADVISORY_LINE`` and ``FAILURE_LINE``)::

    SECURITY.dependency-audit lock=<lock-path> ecosystem=<osv-ecosystem> package=<name>
        version=<exact-locked-version> advisory=<advisory-id> aliases=<id>,<id>
        fix=<version>,<version>
    SECURITY.dependency-audit failure=<kind> path=<repo-path> reason=<text-to-end-of-line>

(the advisory form is wrapped here; it prints as one line).

- Values hold no whitespace, except ``reason``, which runs to the end of the line.
- ``aliases`` (other advisory IDs such as CVE or GHSA) and ``fix`` are comma-separated
  with no spaces and empty (``aliases=``, ``fix=``) when there are none.
- ``ecosystem`` is the OSV ecosystem name (``PyPI``, ``npm``).
- ``failure`` is ``unknown-lock-format``, ``manifest-without-lock``, ``exception`` (any
  exceptions-file rule violation) or ``auditor`` (an auditor that failed, timed out or
  gave malformed output, or a broken auditor pin).

pip-audit supplies the advisory ID, aliases and fixed versions. npm audit supplies the
GHSA ID and its vulnerable range; ``fix`` is then the lowest published release above the
locked version outside that range. A malicious package carries an advisory ID or alias
starting with ``MAL-``: when an auditor marks an advisory malicious (npm: CWE-506 or a
"Malicious ..." title) and no ``MAL-`` record exists for it, the checker adds the alias
``MAL-<advisory-id>``.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import operator
import posixpath
import re
import shlex
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Protocol

from git_inventory import is_fixture_program, repo_paths
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
LOCK = "orchestrator/uv.lock"
AUDIT = "SECURITY.dependency-audit"
EXCEPTIONS = "scripts/checks/dependency-audit-exceptions.json"
EXCEPTION_FIELDS = frozenset(
    {"advisory", "package", "version", "lock", "reason", "exposure", "expires"}
)
ADVISORY_LINE = re.compile(
    r"SECURITY\.dependency-audit lock=(?P<lock>\S+) ecosystem=(?P<ecosystem>\S+)"
    r" package=(?P<package>\S+) version=(?P<version>\S+) advisory=(?P<advisory>\S+)"
    r" aliases=(?P<aliases>\S*) fix=(?P<fix>\S*)"
)
FAILURE_KINDS = ("unknown-lock-format", "manifest-without-lock", "exception", "auditor")
FAILURE_LINE = re.compile(
    r"SECURITY\.dependency-audit failure=(?P<kind>" + "|".join(FAILURE_KINDS) + r")"
    r" path=(?P<path>\S+) reason=(?P<reason>\S[^\r\n]*)"
)
PRETTIER = "3.6.2"


class AuditRunner(Protocol):
    def __call__(
        self, command: list[str], *, stdin: str | None = None, timeout: float
    ) -> subprocess.CompletedProcess[str]: ...


def failure(kind: str, path: str, reason: str) -> str:
    """One non-advisory audit line; ``reason`` is collapsed onto the line."""
    return f"{AUDIT} failure={kind} path={path} reason={' '.join(reason.split())}"


def finding(path: str, rule: str, reason: str, fix: str) -> str:
    clean_reason = " ".join(reason.split()).replace(";", ",")
    return f"{path}: {rule}: {clean_reason}; {fix}"


def option_values(tokens: list[str], option: str) -> set[str]:
    values = {token.split("=", 1)[1] for token in tokens if token.startswith(option + "=")}
    values.update(tokens[index + 1] for index, token in enumerate(tokens[:-1]) if token == option)
    return values


def sync_signature(command: str) -> tuple[str, frozenset[str], frozenset[str], bool]:
    tokens = shlex.split(command)
    extras = option_values(tokens, "--extra")
    if "--all-extras" in tokens:
        extras.add("*")
    project = next(iter(option_values(tokens, "--project")), ".")
    return (
        project,
        frozenset(extras),
        frozenset(option_values(tokens, "--no-extra")),
        bool({"--locked", "--frozen"}.intersection(tokens)),
    )


def dependency_inventory_findings(
    project_toml: str, install_commands: Mapping[str, str]
) -> list[str]:
    declared = set(tomllib.loads(project_toml)["project"].get("optional-dependencies", {}))
    findings = []
    for path, command in install_commands.items():
        _, extras, excluded, _ = sync_signature(command)
        selected = declared if "*" in extras else set(extras)
        unknown = set(extras) - declared - {"*"}
        if selected - excluded != declared or unknown or excluded - declared:
            findings.append(
                finding(
                    path,
                    "PARITY.dependency-inventory",
                    "installer extras differ from declared groups",
                    "select all declared extras with --all-extras and remove unknown exclusions",
                )
            )
    return findings


def guidance_commands(text: str) -> list[str]:
    commands: list[str] = []
    for match in re.finditer(r"```(?:sh|bash|shell)\s*\n(.*?)```", text, re.DOTALL):
        commands.extend(match.group(1).replace("\\\n", " ").splitlines())
    return commands


def install_guidance_findings(
    guidance: Mapping[str, str],
    install_commands: Mapping[str, str],
    make_targets: Mapping[str, str],
) -> list[str]:
    expected = {sync_signature(command) for command in install_commands.values()}
    findings = []
    for path, text in guidance.items():
        for command in guidance_commands(text):
            tokens = shlex.split(command, comments=True)
            if "make" in tokens:
                target = tokens[tokens.index("make") + 1 :]
                if not target:
                    continue
                if target[0] in make_targets:
                    command = make_targets[target[0]]
                elif "setup" in target[0] or "install" in target[0]:
                    command = ""
                else:
                    continue
            elif not re.search(r"\buv\s+sync\b", command):
                continue
            if not command or sync_signature(command) not in expected:
                findings.append(
                    finding(
                        path,
                        "PARITY.install-guidance",
                        "documented installer differs from maintained locked installer",
                        "use make env-setup or the same project, extras "
                        "and --locked/--frozen selection",
                    )
                )
    return findings


def project_requirements(project_toml: str) -> list[Requirement]:
    project = tomllib.loads(project_toml)["project"]
    dependencies = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        dependencies.extend(group)
    return [Requirement(dependency) for dependency in dependencies]


def lock_version_findings(projects: Mapping[str, str], lock_toml: str) -> list[str]:
    packages: dict[str, set[str]] = {}
    for package in tomllib.loads(lock_toml).get("package", []):
        packages.setdefault(canonicalize_name(package["name"]), set()).add(package["version"])
    findings = []
    seen: set[str] = set()
    for path, text in projects.items():
        project = tomllib.loads(text)["project"]
        requirements = project_requirements(text)
        requirements.append(Requirement(f"{project['name']}=={project['version']}"))
        for requirement in requirements:
            if str(requirement) in seen:
                continue
            seen.add(str(requirement))
            versions = packages.get(canonicalize_name(requirement.name), set())
            if not versions or set(requirement.specifier.filter(versions)) != versions:
                findings.append(
                    finding(
                        LOCK,
                        "PARITY.lock-version",
                        f"{path} requirement {requirement} differs from locked packages",
                        "synchronize project metadata and regenerate uv.lock",
                    )
                )
    return findings


def prettier_version_findings(sources: Mapping[str, str]) -> list[str]:
    findings = []
    for path in ("package.json", "package-lock.json"):
        try:
            metadata = json.loads(sources.get(path, "{}"))
            if path == "package.json":
                valid = metadata.get("devDependencies", {}).get("prettier") == PRETTIER
            else:
                packages = metadata.get("packages", {})
                locked = packages.get("node_modules/prettier", {})
                valid = (
                    packages.get("", {}).get("devDependencies", {}).get("prettier") == PRETTIER
                    and locked.get("version") == PRETTIER
                    and bool(locked.get("integrity"))
                    and bool(locked.get("resolved"))
                )
        except (ValueError, AttributeError):
            valid = False
        if not valid:
            findings.append(
                finding(
                    path,
                    "PRETTIER.version",
                    "Prettier is not exactly locked at 3.6.2",
                    "pin prettier 3.6.2 and commit its npm lock record",
                )
            )
    for path, text in sources.items():
        if re.search(r"\bnpx\b[^\n]*\bprettier(?:@[^\s]+)?", text):
            findings.append(
                finding(
                    path,
                    "PRETTIER.version",
                    "npx formatter invocation can fetch outside the locked toolchain",
                    "use node_modules/.bin/prettier from npm ci",
                )
            )
    return findings


def prettier_paths(tracked_paths: Sequence[str]) -> list[str]:
    excluded = {
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        "vendor",
        "__pycache__",
    }
    return sorted(
        path
        for path in tracked_paths
        if Path(path).suffix in {".md", ".yaml", ".yml"}
        and Path(path).parts[0] not in {"docs", "var"}
        and not excluded.intersection(Path(path).parts)
        # Fixture inputs are digested package bytes a stage's tool reads, not maintained prose.
        and not is_fixture_program(path)
        # This index is generated by make ledger-index; ledger-check owns its bytes.
        and path != "lab/ledger/README.md"
    )


def audit_pin_findings(project_toml: str, lock_toml: str) -> list[str]:
    project = tomllib.loads(project_toml)["project"]
    pins = [
        Requirement(item)
        for item in project.get("optional-dependencies", {}).get("dev", [])
        if canonicalize_name(Requirement(item).name) == "pip-audit"
    ]
    if len(pins) != 1 or not re.fullmatch(r"==[^*,]+", str(pins[0].specifier)):
        return [
            failure(
                "auditor",
                "orchestrator/pyproject.toml",
                "pip-audit lacks an exact dev pin: add pip-audit==VERSION to the dev extra "
                "and regenerate uv.lock",
            )
        ]
    versions = [
        package["version"]
        for package in tomllib.loads(lock_toml).get("package", [])
        if canonicalize_name(package["name"]) == "pip-audit"
    ]
    if not versions or any(version not in pins[0].specifier for version in versions):
        return [
            failure(
                "auditor",
                LOCK,
                "locked pip-audit differs from the dev pin: regenerate uv.lock to match it",
            )
        ]
    return []


LOCAL_SOURCES = frozenset({"editable", "directory", "virtual"})
LOCK_ECOSYSTEMS = {
    "uv.lock": "PyPI",
    "poetry.lock": "PyPI",
    "Pipfile.lock": "PyPI",
    "package-lock.json": "npm",
    "npm-shrinkwrap.json": "npm",
    "pnpm-lock.yaml": "npm",
    "yarn.lock": "npm",
    "bun.lock": "npm",
    "bun.lockb": "npm",
    "Cargo.lock": "crates.io",
    "go.sum": "Go",
    "Gemfile.lock": "RubyGems",
    "composer.lock": "Packagist",
}
MANIFESTS = {
    "pyproject.toml": "PyPI",
    "package.json": "npm",
    "Cargo.toml": "crates.io",
    "go.mod": "Go",
    "Gemfile": "RubyGems",
    "composer.json": "Packagist",
}
NPM_LOCKS = frozenset({"package-lock.json", "npm-shrinkwrap.json"})
NPM_DEPENDENCY_KEYS = (
    "dependencies",
    "devDependencies",
    "optionalDependencies",
    "peerDependencies",
)
TOKEN = re.compile(r"[^\s;,]+")
SEMVER = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?")
COMPARATOR = re.compile(r"(<=|>=|<|>|=)?(.+)")
COMPARISONS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "=": operator.eq,
}
GHSA = re.compile(r"GHSA(?:-[23456789cfghjmpqrvwx]{4}){3}")
SemVer = tuple[int, int, int, bool, str]


class AuditError(Exception):
    """An auditor could not produce a complete, well-formed report for one lock."""


@dataclass(frozen=True)
class Advisory:
    lock: str
    ecosystem: str
    package: str
    version: str
    advisory: str
    aliases: tuple[str, ...]
    fixes: tuple[str, ...]


@dataclass(frozen=True)
class LockReport:
    inventory: dict[str, set[str]]
    advisories: list[Advisory]


def lock_ecosystem(path: str) -> str | None:
    name = PurePosixPath(path).name
    if name.startswith("requirements") and name.endswith(".txt"):
        return "PyPI"
    return LOCK_ECOSYSTEMS.get(name)


def package_key(ecosystem: str, name: str) -> str:
    return canonicalize_name(name) if ecosystem == "PyPI" else name


def parent(path: str) -> str:
    return PurePosixPath(path).parent.as_posix()


def declares_dependencies(name: str, text: str) -> bool:
    if name in {"package.json", "composer.json"}:
        data = json.loads(text)
        keys = ("require", "require-dev") if name == "composer.json" else NPM_DEPENDENCY_KEYS
        return any(data.get(key) for key in keys)
    if name == "go.mod":
        return re.search(r"^\s*require\b", text, re.MULTILINE) is not None
    if name == "Gemfile":
        return re.search(r"^\s*gem\s", text, re.MULTILINE) is not None
    data = tomllib.loads(text)
    if name == "Cargo.toml":
        tables = [data, data.get("workspace", {}), *data.get("target", {}).values()]
        return any(v for table in tables for k, v in table.items() if k.endswith("dependencies"))
    project = data.get("project", {})
    poetry = data.get("tool", {}).get("poetry", {})
    return any(
        [
            project.get("dependencies"),
            *project.get("optional-dependencies", {}).values(),
            *data.get("dependency-groups", {}).values(),
            set(poetry.get("dependencies", {})) - {"python"},
            *(group.get("dependencies") for group in poetry.get("group", {}).values()),
        ]
    )


def local_paths(source: Mapping[str, str]) -> list[str]:
    return [source[kind] for kind in sorted(LOCAL_SOURCES.intersection(source))]


def lock_members(lock: str, text: str) -> set[str]:
    """Other directories whose manifests a lock pins: uv path sources and npm workspaces."""
    name = PurePosixPath(lock).name
    if name == "uv.lock":
        sources = [package.get("source", {}) for package in tomllib.loads(text).get("package", [])]
        relative = [path for source in sources for path in local_paths(source)]
    elif name in NPM_LOCKS:
        relative = [key for key in json.loads(text).get("packages", {}) if key]
        relative = [key for key in relative if "node_modules/" not in key]
    else:
        return set()
    return {posixpath.normpath(posixpath.join(parent(lock), path)) for path in relative}


def coverage_findings(root: Path, locks: Sequence[str], manifests: Sequence[str]) -> list[str]:
    """Manifests with dependencies that no lock of their ecosystem pins."""
    covered = {(parent(lock), lock_ecosystem(lock)) for lock in locks}
    for lock in locks:
        try:
            members = lock_members(lock, (root / lock).read_text())
        except (OSError, ValueError, AttributeError, TypeError):
            continue  # The lock's own audit reports why it cannot be read.
        covered |= {(member, lock_ecosystem(lock)) for member in members}
    findings = []
    for path in manifests:
        if (parent(path), MANIFESTS[PurePosixPath(path).name]) in covered:
            continue
        try:
            declared = declares_dependencies(PurePosixPath(path).name, (root / path).read_text())
        except (OSError, ValueError, AttributeError, TypeError):
            findings.append(
                failure(
                    "manifest-without-lock",
                    path,
                    "unreadable manifest with no lock of its ecosystem: repair the manifest",
                )
            )
            continue
        if declared:
            findings.append(
                failure(
                    "manifest-without-lock",
                    path,
                    "dependencies are declared with no lock of their ecosystem: commit a lock "
                    "file beside the manifest or pin it from a workspace lock",
                )
            )
    return findings


def checked(item: Advisory) -> Advisory:
    """Refuse auditor values that would break the advisory line; drop the ID from aliases."""
    values = [item.package, item.version, item.advisory, *item.aliases, *item.fixes]
    if not all(isinstance(value, str) and TOKEN.fullmatch(value) for value in values):
        raise AuditError("auditor returned a malformed advisory field")
    return replace(item, aliases=tuple(sorted(set(item.aliases) - {item.advisory})))


def audit_report_problem(text: str) -> str:
    try:
        report = json.loads(text)
    except ValueError:
        return "auditor returned invalid JSON"
    if (
        not isinstance(report, dict)
        or not isinstance(report.get("dependencies"), list)
        or not report["dependencies"]
    ):
        return "auditor returned no usable dependency inventory"
    for dependency in report["dependencies"]:
        if (
            not isinstance(dependency, dict)
            or not dependency.get("name")
            or not dependency.get("version")
            or not isinstance(dependency.get("vulns"), list)
            or dependency.get("skip_reason")
        ):
            return "auditor skipped or could not resolve a dependency"
        for vulnerability in dependency["vulns"]:
            if (
                not isinstance(vulnerability, dict)
                or not vulnerability.get("id")
                or not isinstance(vulnerability.get("fix_versions"), list)
                or not isinstance(vulnerability.get("aliases", []), list)
            ):
                return "auditor returned a malformed vulnerability"
    return ""


def checked_exit(result: subprocess.CompletedProcess[str], advisories: list[Advisory]) -> None:
    if result.returncode not in (0, 1) or (result.returncode and not advisories):
        detail = result.stderr.strip()
        raise AuditError(
            "auditor returned a nonzero exit status" + (": " + detail if detail else "")
        )


def pip_audit(lock: str, requirements: str, run: AuditRunner) -> LockReport:
    audited = run(
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
        stdin=requirements,
        timeout=300,
    )
    problem = audit_report_problem(audited.stdout)
    if problem:
        raise AuditError(problem)
    dependencies = json.loads(audited.stdout)["dependencies"]
    advisories = [
        checked(
            Advisory(
                lock,
                "PyPI",
                dependency["name"],
                dependency["version"],
                vulnerability["id"],
                tuple(vulnerability.get("aliases", [])),
                tuple(vulnerability["fix_versions"]),
            )
        )
        for dependency in dependencies
        for vulnerability in dependency["vulns"]
    ]
    checked_exit(audited, advisories)
    inventory: dict[str, set[str]] = {}
    for dependency in dependencies:
        key = package_key("PyPI", dependency["name"])
        inventory.setdefault(key, set()).add(dependency["version"])
    return LockReport(inventory, advisories)


def audit_uv(root: Path, lock: str, run: AuditRunner) -> LockReport:
    packages = tomllib.loads((root / lock).read_text()).get("package", [])
    if all(LOCAL_SOURCES.intersection(package.get("source", {})) for package in packages):
        return LockReport({}, [])
    exported = run(
        [
            "uv",
            "export",
            "--project",
            parent(lock),
            "--locked",
            "--all-extras",
            "--all-groups",
            "--all-packages",
            "--format",
            "requirements-txt",
            "--no-emit-local",
        ],
        timeout=120,
    )
    if exported.returncode or not exported.stdout.strip():
        raise AuditError("locked requirements export failed or was empty")
    return pip_audit(lock, exported.stdout, run)


def audit_requirements(root: Path, lock: str, run: AuditRunner) -> LockReport:
    return pip_audit(lock, (root / lock).read_text(), run)


def semver(version: str) -> SemVer:
    match = SEMVER.fullmatch(version.strip())
    if match is None:
        raise AuditError(f"auditor returned an unparseable version {version}")
    major, minor, patch, pre = match.groups()
    return int(major), int(minor), int(patch), pre is None, pre or ""


def satisfies(version: SemVer, comparator: str) -> bool:
    match = COMPARATOR.fullmatch(comparator)
    if comparator == "*" or match is None:
        return comparator == "*"
    return bool(COMPARISONS[match.group(1) or "="](version, semver(match.group(2))))


def in_range(version: SemVer, spec: str) -> bool:
    """Evaluate an advisory's npm range: ``||`` of space-joined comparators."""
    return any(
        all(
            satisfies(version, comparator)
            for comparator in re.sub(r"(<=|>=|<|>|=)\s+", r"\1", alternative).split()
        )
        for alternative in spec.split("||")
    )


def npm_fixes(installed: str, vulnerable: str, published: Sequence[str]) -> tuple[str, ...]:
    """The lowest published release above ``installed`` outside the advisory's range."""
    current = semver(installed)
    releases = sorted((semver(item), item) for item in published if SEMVER.fullmatch(item))
    for key, version in releases:
        if key[3] and key > current and not in_range(key, vulnerable):
            return (version,)
    return ()


def npm_published(name: str, directory: str, run: AuditRunner) -> list[str]:
    viewed = run(["npm", "view", name, "versions", "--json", "--prefix", directory], timeout=120)
    try:
        versions = json.loads(viewed.stdout)
    except ValueError:
        versions = None
    versions = [versions] if isinstance(versions, str) else versions
    if viewed.returncode or not isinstance(versions, list):
        raise AuditError(f"npm view could not list published versions of {name}")
    return [str(version) for version in versions]


NpmAdvisory = tuple[str, str, tuple[str, ...]]
NpmSource = tuple[str, list[str], list[NpmAdvisory]]
MALICIOUS_CWE = "CWE-506"


def malicious_aliases(ident: str, aliases: Sequence[str], marked: bool) -> tuple[str, ...]:
    """Add ``MAL-<id>`` when an auditor marks an advisory malicious without a MAL- record."""
    known = (ident, *aliases)
    if marked and not any(item.startswith("MAL-") for item in known):
        return (*aliases, f"MAL-{ident}")
    return tuple(aliases)


def npm_marks_malicious(via: Mapping[str, object]) -> bool:
    """Npm marks malware advisories with CWE-506 (embedded malicious code) or their title."""
    cwe = via.get("cwe", [])
    title = str(via.get("title", ""))
    return (isinstance(cwe, list) and MALICIOUS_CWE in cwe) or title.lower().startswith("malicious")


def npm_sources(text: str) -> list[NpmSource]:
    """Validated (package, lock nodes, [(GHSA ID, vulnerable range, aliases)]) per source."""
    try:
        report = json.loads(text)
    except ValueError:
        raise AuditError("auditor returned invalid JSON") from None
    if (
        not isinstance(report, dict)
        or "error" in report
        or report.get("auditReportVersion") != 2
        or not isinstance(report.get("vulnerabilities"), dict)
    ):
        raise AuditError("auditor returned no usable audit report")
    sources: list[NpmSource] = []
    for name, entry in report["vulnerabilities"].items():
        advisories = []
        for via in entry["via"]:
            if isinstance(via, str):
                continue  # Vulnerable through a dependency, which carries its own advisory.
            ghsa = GHSA.search(str(via.get("url", "")))
            if ghsa is None or not isinstance(via.get("range"), str):
                raise AuditError("auditor returned a malformed vulnerability")
            aliases = malicious_aliases(ghsa.group(), (), npm_marks_malicious(via))
            advisories.append((ghsa.group(), via["range"], aliases))
        if advisories and (not isinstance(entry["nodes"], list) or not entry["nodes"]):
            raise AuditError(f"auditor named no lock node for {name}")
        if advisories:
            sources.append((str(name), [str(node) for node in entry["nodes"]], advisories))
    return sources


def npm_inventory(lock: str, packages: object) -> tuple[dict[str, str], dict[str, set[str]]]:
    if not isinstance(packages, dict):
        raise AuditError(f"{lock} has no packages map (lockfileVersion 2 or later)")
    nodes = {
        str(key): str(entry["version"])
        for key, entry in packages.items()
        if "node_modules/" in key and "version" in entry
    }
    inventory: dict[str, set[str]] = {}
    for key, version in nodes.items():
        name = packages[key].get("name") or key.rsplit("node_modules/", 1)[1]
        inventory.setdefault(str(name), set()).add(version)
    return nodes, inventory


def audit_npm(root: Path, lock: str, run: AuditRunner) -> LockReport:
    directory = parent(lock)
    nodes, inventory = npm_inventory(lock, json.loads((root / lock).read_text()).get("packages"))
    if not inventory:
        return LockReport({}, [])
    audited = run(
        [
            "npm",
            "audit",
            "--json",
            "--package-lock-only",
            "--include=dev",
            "--include=optional",
            "--include=peer",
            "--prefix",
            directory,
        ],
        timeout=300,
    )
    advisories = []
    for name, entry_nodes, sources in npm_sources(audited.stdout):
        if not set(entry_nodes) <= set(nodes):
            raise AuditError(f"auditor named {name} outside the lock inventory")
        published = npm_published(name, directory, run)
        versions = sorted({nodes[node] for node in entry_nodes})
        advisories += [
            checked(
                Advisory(
                    lock,
                    "npm",
                    name,
                    version,
                    ghsa,
                    aliases,
                    npm_fixes(version, vulnerable, published),
                )
            )
            for version, (ghsa, vulnerable, aliases) in itertools.product(versions, sources)
        ]
    checked_exit(audited, advisories)
    return LockReport(inventory, advisories)


AUDITORS = {
    "uv.lock": audit_uv,
    "package-lock.json": audit_npm,
    "npm-shrinkwrap.json": audit_npm,
}


def audit_lock(root: Path, lock: str, run: AuditRunner) -> LockReport:
    name = PurePosixPath(lock).name
    auditor = AUDITORS.get(name) or audit_requirements
    try:
        return auditor(root, lock, run)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AuditError(f"audit tool failed ({type(error).__name__})") from None
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise AuditError(f"unreadable lock or report ({type(error).__name__})") from None


def advisory_finding(item: Advisory) -> str:
    return (
        f"{AUDIT} lock={item.lock} ecosystem={item.ecosystem} package={item.package}"
        f" version={item.version} advisory={item.advisory}"
        f" aliases={','.join(item.aliases)} fix={','.join(item.fixes)}"
    )


def exception_problem(entry: object, today: date) -> str:
    if not isinstance(entry, dict):
        return "is not an object"
    missing = sorted(EXCEPTION_FIELDS - set(entry))
    extra = sorted(set(entry) - EXCEPTION_FIELDS)
    if missing or extra:
        return f"fields differ: missing {missing or 'none'} extra {extra or 'none'}"
    if not all(isinstance(value, str) and value.strip() for value in entry.values()):
        return "fields must be non-empty strings"
    try:
        expires = date.fromisoformat(entry["expires"])
    except ValueError:
        return f"expires {entry['expires']} is not an ISO date"
    return f"expired on {expires.isoformat()}" if expires < today else ""


def exception_findings(
    advisories: list[Advisory],
    reports: Mapping[str, LockReport],
    text: str,
    today: date,
) -> tuple[list[Advisory], list[str]]:
    """Suppress advisories that a valid owner-approved exception names; report invalid ones."""

    def report(reason: str) -> str:
        return failure("exception", EXCEPTIONS, reason)

    try:
        data = json.loads(text)
    except ValueError:
        return advisories, [report("exceptions file is not valid JSON")]
    if (
        not isinstance(data, dict)
        or set(data) != {"schemaVersion", "exceptions"}
        or data["schemaVersion"] != 1
        or not isinstance(data["exceptions"], list)
    ):
        return advisories, [
            report("exceptions file must hold schemaVersion 1 and an exceptions list")
        ]
    suppressed: set[Advisory] = set()
    findings = []
    for index, entry in enumerate(data["exceptions"]):
        problem = exception_problem(entry, today)
        if not problem:
            problem, matched = exception_match(entry, advisories, reports, suppressed)
            suppressed |= matched
        if problem:
            findings.append(report(f"exception {index} {problem}"))
    return [item for item in advisories if item not in suppressed], findings


def exception_match(
    entry: Mapping[str, str],
    advisories: Sequence[Advisory],
    reports: Mapping[str, LockReport],
    suppressed: set[Advisory],
) -> tuple[str, set[Advisory]]:
    lock, version = entry["lock"], entry["version"]
    if lock not in reports:
        return f"names {lock}, which has no completed audit", set()
    key = package_key(lock_ecosystem(lock) or "", entry["package"])
    if version not in reports[lock].inventory.get(key, set()):
        return f"version {entry['package']}=={version} no longer matches {lock}", set()
    matched = {
        item
        for item in advisories
        if item.lock == lock
        and package_key(item.ecosystem, item.package) == key
        and item.version == version
        and entry["advisory"] in (item.advisory, *item.aliases)
    }
    fixed = sorted({fix for item in matched for fix in item.fixes})
    if not matched:
        return f"{entry['advisory']} matches no current finding", set()
    if fixed:
        return f"{entry['advisory']} now has a fix ({', '.join(fixed)})", set()
    if matched & suppressed:
        return f"{entry['advisory']} duplicates another exception", set()
    return "", matched


def audit_findings(root: Path, paths: Sequence[str], *, run: AuditRunner, today: date) -> list[str]:
    """Audit every discovered lock; see the module docstring for the finding format."""
    maintained = [path for path in paths if not is_fixture_program(path)]
    locks = sorted(path for path in maintained if lock_ecosystem(path))
    manifests = sorted(path for path in maintained if PurePosixPath(path).name in MANIFESTS)
    findings = coverage_findings(root, locks, manifests)
    reports: dict[str, LockReport] = {}
    for lock in locks:
        name = PurePosixPath(lock).name
        if name not in AUDITORS and not name.startswith("requirements"):
            findings.append(failure("unknown-lock-format", lock, f"no auditor for {name}: add one"))
            continue
        try:
            reports[lock] = audit_lock(root, lock, run)
        except AuditError as problem:
            findings.append(failure("auditor", lock, str(problem)))
    advisories = [item for report in reports.values() for item in report.advisories]
    try:
        text = (root / EXCEPTIONS).read_text()
    except OSError:
        text = ""
    remaining, invalid = exception_findings(advisories, reports, text, today)
    return [*sorted(findings), *map(advisory_finding, remaining), *invalid]


def run_process(
    command: list[str], *, stdin: str | None = None, timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def maintained_installers(sources: Mapping[str, str]) -> dict[str, str]:
    installers = {}
    for path, text in sources.items():
        for index, line in enumerate(text.replace("\\\n", " ").splitlines(), 1):
            if re.search(r"\buv\s+sync\b", line) and not line.lstrip().startswith("#"):
                installers[f"{path}:{index}"] = line.strip()
    return installers


def static_findings(paths: list[str]) -> list[str]:
    project_text = (ROOT / "orchestrator/pyproject.toml").read_text()
    lock_text = (ROOT / LOCK).read_text()
    source_paths = sorted(
        {
            path
            for path in paths
            if not path.startswith(("docs/", "var/"))
            and (
                path.startswith(".github/workflows/") or path.endswith(".sh") or path == "Makefile"
            )
        }
        | {"package.json", "package-lock.json"}
    )
    sources = {path: (ROOT / path).read_text() for path in source_paths}
    makefile = sources["Makefile"]
    setup = re.search(r"^env-setup:\n\t([^\n]+)", makefile, re.MULTILINE)
    if setup is None:
        raise ValueError("Makefile has no env-setup installer")
    sync_args = shlex.split(setup.group(1).replace("--sync-arg ", ""))
    setup_command = "uv sync --project orchestrator --frozen " + " ".join(
        sync_args[sync_args.index("setup") + 1 :]
    )
    installers = maintained_installers(sources)
    installers["Makefile"] = setup_command
    projects = {
        path: (ROOT / path).read_text()
        for path in ("orchestrator/pyproject.toml", "contract/pyproject.toml")
    }
    return [
        *dependency_inventory_findings(project_text, installers),
        *install_guidance_findings(
            {"README.md": (ROOT / "README.md").read_text()},
            installers,
            {"env-setup": setup_command},
        ),
        *lock_version_findings(projects, lock_text),
        *prettier_version_findings(sources),
        *audit_pin_findings(project_text, lock_text),
    ]


def frozen_exclusions(
    released: Mapping[str, str],
    current: Mapping[str, str],
    payload_paths: Sequence[str],
) -> list[str]:
    """Freeze the grading payload and every package whose bytes match its latest release."""
    return sorted(
        set(payload_paths)
        | {folder for folder, digest in released.items() if current.get(folder) == digest}
    )


def formatter_scope(paths: Sequence[str]) -> list[str]:
    """Exclude I2's byte-frozen packages and grading payload."""
    from raidar.contract.paths import agents_root, releases_file, workflows_root
    from raidar.contract.releases import ReleaseKind, load_register
    from raidar.grading.payload import runtime_files
    from raidar.subject.agent.definition import load_agent
    from raidar.subject.package.registry import load_stage_package
    from raidar.subject.releases import tree_workflow_digest

    register = load_register(releases_file(ROOT))
    released: dict[str, str] = {}
    current: dict[str, str] = {}
    for kind in ReleaseKind:
        for name in register.names(kind):
            latest = register.latest(kind, name)
            if latest is None:
                raise ValueError(f"no latest release for {kind}/{name}")
            if kind is ReleaseKind.AGENT:
                folder = agents_root(ROOT) / f"{name}.yaml"
                digest = load_agent(agents_root(ROOT), name).digest
            elif kind is ReleaseKind.WORKFLOW:
                folder = workflows_root(ROOT) / name
                digest = tree_workflow_digest(name, ROOT, register)
            else:
                folder = ROOT / "lab/stages" / name
                digest = load_stage_package(folder).digest
            relative = folder.relative_to(ROOT).as_posix()
            released[relative] = register.of(kind)[latest]
            current[relative] = digest
    payload = [path.relative_to(ROOT).as_posix() for path in runtime_files().values()]
    frozen = frozen_exclusions(released, current, payload)
    return [
        path
        for path in prettier_paths(paths)
        if not (ROOT / path).is_symlink()
        and not any(path == prefix or path.startswith(prefix + "/") for prefix in frozen)
    ]


def formatter_findings(paths: Sequence[str]) -> list[str]:
    selected = formatter_scope(paths)
    result = run_process(
        [str(ROOT / "node_modules/.bin/prettier"), "--list-different", *selected],
        timeout=120,
    )
    if result.returncode not in (0, 1):
        raise ValueError("Prettier scan failed: " + result.stderr.strip())
    different = result.stdout.splitlines()
    if any(path not in selected for path in different):
        raise ValueError("Prettier reported a path outside its maintained scope")
    return [
        finding(
            path,
            "PRETTIER.format",
            "unformatted bytes sha256=" + hashlib.sha256((ROOT / path).read_bytes()).hexdigest(),
            "format with the locked Prettier unless an intentional identity change is required",
        )
        for path in different
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("static", "prettier", "audit"))
    args = parser.parse_args()
    try:
        paths = repo_paths(ROOT)
        if args.mode == "static":
            findings = static_findings(paths)
        elif args.mode == "audit":
            findings = audit_findings(ROOT, paths, run=run_process, today=date.today())
        else:
            findings = formatter_findings(paths)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        if args.mode == "audit":
            print(failure("auditor", "scripts/checks/check-toolchain-parity.py", str(error)))
            return 1
        rule = "PARITY.lock-version" if args.mode == "static" else "PRETTIER.version"
        print(
            finding(
                "scripts/checks/check-toolchain-parity.py",
                rule,
                str(error).replace("\n", " "),
                "repair the locked toolchain or input files",
            )
        )
        return 1
    print("\n".join(findings)) if findings else None
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
