#!/usr/bin/env python3
"""Check maintained installer, lock, formatter and dependency-audit parity."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import runpy
import shlex
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
LOCK = "orchestrator/uv.lock"
AUDIT = "SECURITY.dependency-audit"
PRETTIER = "3.6.2"
FrozenExclusions = Callable[
    [Mapping[str, str], Mapping[str, str], Sequence[str]], list[str]
]


class AuditRunner(Protocol):
    def __call__(
        self, command: list[str], *, stdin: str | None = None, timeout: float
    ) -> subprocess.CompletedProcess[str]: ...


def finding(path: str, rule: str, reason: str, fix: str) -> str:
    clean_reason = " ".join(reason.split()).replace(";", ",")
    return f"{path}: {rule}: {clean_reason}; {fix}"


def option_values(tokens: list[str], option: str) -> set[str]:
    values = {
        token.split("=", 1)[1] for token in tokens if token.startswith(option + "=")
    }
    values.update(
        tokens[index + 1] for index, token in enumerate(tokens[:-1]) if token == option
    )
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
    declared = set(
        tomllib.loads(project_toml)["project"].get("optional-dependencies", {})
    )
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
        packages.setdefault(canonicalize_name(package["name"]), set()).add(
            package["version"]
        )
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
                    packages.get("", {}).get("devDependencies", {}).get("prettier")
                    == PRETTIER
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
            finding(
                "orchestrator/pyproject.toml",
                AUDIT,
                "pip-audit lacks an exact dev pin",
                "add pip-audit==VERSION to the dev extra and regenerate uv.lock",
            )
        ]
    versions = [
        package["version"]
        for package in tomllib.loads(lock_toml).get("package", [])
        if canonicalize_name(package["name"]) == "pip-audit"
    ]
    if not versions or any(version not in pins[0].specifier for version in versions):
        return [
            finding(
                LOCK,
                AUDIT,
                "locked pip-audit differs from the dev pin",
                "regenerate uv.lock to match the exact dev pin",
            )
        ]
    return []


def dependency_path(path: str) -> bool:
    name = Path(path).name
    return name in {
        "pyproject.toml",
        "uv.lock",
        "package.json",
        "package-lock.json",
    } or (name.startswith("requirements") and name.endswith(".txt"))


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
            ):
                return "auditor returned a malformed vulnerability"
    return ""


def vulnerability_findings(text: str) -> list[str]:
    report = json.loads(text)
    return [
        finding(
            LOCK,
            AUDIT,
            f"{dependency['name']}=={dependency['version']} has {vulnerability['id']}",
            "upgrade to "
            + (", ".join(vulnerability["fix_versions"]) or "a patched release"),
        )
        for dependency in report["dependencies"]
        for vulnerability in dependency["vulns"]
    ]


def audit_findings(
    changed_paths: Sequence[str], *, ci: bool, run: AuditRunner
) -> list[str]:
    if not ci and not any(dependency_path(path) for path in changed_paths):
        return []
    problem = ""
    try:
        exported = run(
            [
                "uv",
                "export",
                "--project",
                "orchestrator",
                "--locked",
                "--all-extras",
                "--format",
                "requirements-txt",
                "--no-emit-local",
                # The pinned guardrails reference is a hash-less git requirement that
                # pip-audit --no-deps refuses; its locked dependencies are still exported.
                "--no-emit-package",
                "omp-guardrails",
            ],
            timeout=120,
        )
        if exported.returncode or not exported.stdout.strip():
            problem = "locked requirements export failed or was empty"
        else:
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
                stdin=exported.stdout,
                timeout=300,
            )
            problem = audit_report_problem(audited.stdout)
            if not problem:
                vulnerabilities = vulnerability_findings(audited.stdout)
                if audited.returncode in (0, 1) and vulnerabilities:
                    return vulnerabilities
            if audited.returncode:
                problem = problem or "auditor returned a nonzero exit status"
                if audited.stderr.strip():
                    problem += ": " + audited.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        problem = f"audit tool failed ({type(error).__name__})"
    return (
        [
            finding(
                LOCK,
                AUDIT,
                problem,
                "resolve dependency vulnerabilities or tool/report errors "
                "and rerun the strict locked audit",
            )
        ]
        if problem
        else []
    )


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


def tracked_paths() -> list[str]:
    result = run_process(["git", "ls-files", "-z"], timeout=30)
    result.check_returncode()
    return [path for path in result.stdout.split("\0") if path]


def changed_paths() -> list[str]:
    result = run_process(
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACDMRTUXB"],
        timeout=30,
    )
    result.check_returncode()
    return [path for path in result.stdout.split("\0") if path]


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
                path.startswith(".github/workflows/")
                or path.endswith(".sh")
                or path == "Makefile"
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


def formatter_scope(paths: Sequence[str]) -> list[str]:
    """Exclude I2's byte-frozen packages and grading payload via its shared policy."""
    from raidar.contract.paths import agents_root, releases_file, workflows_root
    from raidar.contract.releases import ReleaseKind, load_register
    from raidar.grading.payload import runtime_files
    from raidar.subject.agent.definition import load_agent
    from raidar.subject.package.registry import load_stage_package
    from raidar.subject.releases import tree_workflow_digest

    policy = runpy.run_path(str(ROOT / "scripts/checks/check-identity.py"))
    exclusions = cast("FrozenExclusions", policy["frozen_exclusions"])
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
    frozen = exclusions(released, current, payload)
    return [
        path
        for path in prettier_paths(paths)
        if not (ROOT / path).is_symlink()
        and not any(
            path == prefix or path.startswith(prefix + "/") for prefix in frozen
        )
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
            "unformatted bytes sha256="
            + hashlib.sha256((ROOT / path).read_bytes()).hexdigest(),
            "format with the locked Prettier unless an intentional identity change is required",
        )
        for path in different
    ]


def baseline_findings(mode: str, findings: Sequence[str]) -> int:
    tool = "dependency-audit" if mode == "audit" else "prettier"
    report = ROOT / "var/quality/toolchain" / f"{tool}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for line in findings:
        path, code, source_line = line.split(": ", 2)
        records.append(
            {"path": path, "code": code, "symbol": "", "source_line": source_line}
        )
    report.write_text(json.dumps(records) + "\n")
    result = run_process(
        [
            sys.executable,
            "scripts/checks/check-finding-baseline.py",
            "--tool",
            tool,
            "--format",
            "findings-json",
            "--report",
            str(report),
            "--baseline",
            f".omp/baselines/{tool}.json",
        ],
        timeout=60,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("static", "prettier", "audit"))
    parser.add_argument("--ci", action="store_true")
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    try:
        paths = tracked_paths()
        if args.mode == "static":
            findings = static_findings(paths)
        elif args.mode == "audit":
            changed = args.paths or changed_paths()
            if not args.ci and not any(dependency_path(path) for path in changed):
                return 0
            findings = audit_findings(changed, ci=args.ci, run=run_process)
        else:
            findings = formatter_findings(paths)
        if args.baseline and args.mode != "static":
            return baseline_findings(args.mode, findings)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        rules = {
            "static": "PARITY.lock-version",
            "prettier": "PRETTIER.version",
            "audit": AUDIT,
        }
        rule = rules[args.mode]
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
