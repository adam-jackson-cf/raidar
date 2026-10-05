"""guardrail-check: the single authoritative python-guardrails gate.

Contract (the repository's pre-commit hook runs it; contributors can run it too):

    guardrail-check [--root DIR] [--json-out FILE | --json-dir DIR] [--fix]

- Check-only by default. Ruff and mypy always run with the protected configs in
  ``$GUARDRAILS_HOME/config`` (``--config`` / ``--config-file``), so workspace
  copies can be edited without loosening the gate.
- The ``tests`` check (``guardrails.tests_check``) runs the workspace's own
  tests, like a repository's commit check: ``unittest`` always, pytest for
  pytest-style tests, both under coverage, in a throwaway copy of the
  workspace. A change to production code with no test that ran is refused
  (``TEST.missing``), so are failing tests (``TEST.failed``) and owned line
  coverage below 85% (``TEST.coverage-minimum``).
- Exit 0: no blocking finding (warnings may be printed). Exit 1: refused, at
  least one blocking finding. Exit 126: the gate itself could not run (missing or
  crashed tool, timeout, unreadable root); a gate error is never a pass. 126 is
  the shell's "cannot execute" code, so a hook runner treats it as not runnable.
- stdout: a verdict line, one ``gate-check <name> pass|fail`` line per check
  (machine-readable), then blocking findings and warnings, one
  ``path:line:col: CODE message`` per line, capped per section.
- ``--json-out FILE`` or ``--json-dir DIR`` (writes ``DIR/gate-<n>.json``, next free
  n): the full report (verdict, counts by code, every finding with its tier and
  source, per-tool durations, and whether the workspace's ``ruff.toml`` and
  ``mypy.ini`` copies still match the protected configs). Without either flag the
  report goes to ``$RAIDAR_GATE_REPORT_DIR`` when the hook runner sets it, so the
  hook command stays plain ``guardrail-check``. The report is never printed;
  failing to write it never changes the verdict.
- ``--fix``: explicit opt-in. Runs ``ruff check --fix`` and ``ruff format``
  with the protected config, then the normal check. Never used by the step-end
  gate.
- ``--version``: the pack and its pinned tools' versions.

No cache is read or written (Ruff ``--no-cache``, mypy ``--cache-dir=/dev/null``):
a cache in a directory a contributor can write could be forged, and the gate
decides from the working tree alone. Any unexpected failure is a gate error (exit 126), never
a refusal or a pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from guardrails.diagnostics import Diagnostic
from guardrails.fanout import fanout_diagnostics
from guardrails.scope import SourceFile, discover
from guardrails.source_rules import source_diagnostics
from guardrails.structure import structure_diagnostics
from guardrails.tests_check import TestsGateError, tests_findings

PACK = "python-guardrails@3"
REPORT_SCHEMA = "python-guardrails.gate-report/1"
REPORT_DIR_ENV = "RAIDAR_GATE_REPORT_DIR"
TOOL_TIMEOUT_SEC = 120
PRINT_CAP = 60
GATE_ERROR_EXIT = 126
CHECKS = ("structure", "source-rules", "fanout", "ruff", "ruff-format", "mypy", "uv", "tests")
# Workspace copies of the protected configs (identical bytes when the trial starts).
WORKSPACE_CONFIGS = ("ruff.toml", "mypy.ini")
_MYPY_LINE = re.compile(
    r"^(?P<path>[^:]+):(?P<line>\d+):(?:(?P<col>\d+):)? (?P<sev>error|note): "
    r"(?P<msg>.*?)(?:  \[(?P<code>[a-z0-9-]+)\])?$"
)


class GateError(RuntimeError):
    """The gate could not produce a trustworthy verdict."""


@dataclass
class ToolRun:
    """Timing and identity of one external tool call."""

    name: str
    seconds: float
    returncode: int
    version: str = ""


@dataclass
class GateResult:
    """Everything one gate run found."""

    findings: list[tuple[Diagnostic, str]] = field(default_factory=list)
    tools: list[ToolRun] = field(default_factory=list)
    files_checked: int = 0
    production_files: int = 0
    tests: dict[str, object] | None = None


def _home() -> Path:
    return Path(os.environ.get("GUARDRAILS_HOME", Path(__file__).resolve().parents[2]))


def _tool(name: str) -> str:
    candidate = _home() / "venv" / "bin" / name
    if candidate.is_file():
        return str(candidate)
    raise GateError(f"{name} is not installed at {candidate}")


def _run(
    name: str, argv: Sequence[str], cwd: Path, result: GateResult
) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=TOOL_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise GateError(f"{name} timed out after {TOOL_TIMEOUT_SEC}s") from exc
    except OSError as exc:
        raise GateError(f"{name} could not start: {exc}") from exc
    result.tools.append(ToolRun(name, round(time.monotonic() - started, 3), completed.returncode))
    return completed


def load_policy() -> dict[str, str]:
    """Return ``{code prefix: tier}``; the longest matching prefix wins, else ``default``."""
    data = tomllib.loads((_home() / "config" / "policy.toml").read_text(encoding="utf-8"))
    tiers: dict[str, str] = {"": data.get("default", "block")}
    for tier in ("block", "warn", "off"):
        for prefix in data.get(tier, []):
            tiers[prefix] = tier
    return tiers


def tier_for(code: str, policy: dict[str, str]) -> str:
    """Return the tier of the longest policy prefix that matches the code."""
    best = max((prefix for prefix in policy if code.startswith(prefix)), key=len)
    return policy[best]


def _ruff_check(root: Path, result: GateResult) -> list[Diagnostic]:
    config = _home() / "config" / "ruff.toml"
    completed = _run(
        "ruff-check",
        [
            _tool("ruff"),
            "check",
            "--config",
            str(config),
            "--no-cache",
            "--output-format",
            "json",
            "--no-fix",
            "--exit-zero",
            ".",
        ],
        root,
        result,
    )
    if completed.returncode != 0:
        raise GateError(f"ruff check failed: {completed.stderr.strip()[-400:]}")
    found: list[Diagnostic] = []
    for item in json.loads(completed.stdout or "[]"):
        relpath = Path(item["filename"]).resolve().relative_to(root).as_posix()
        code = item.get("code") or "E999"
        location = item.get("location") or {}
        found.append(
            Diagnostic(
                code,
                relpath,
                int(location.get("row", 1)),
                item.get("message", ""),
                col=int(location.get("column", 1)),
                source="ruff",
            )
        )
    return found


def _ruff_format(root: Path, result: GateResult) -> list[Diagnostic]:
    config = _home() / "config" / "ruff.toml"
    completed = _run(
        "ruff-format",
        [
            _tool("ruff"),
            "format",
            "--check",
            "--config",
            str(config),
            "--no-cache",
            ".",
        ],
        root,
        result,
    )
    # Unparsable files are reported by PYS000; ruff format exits 2 on them.
    unparsable = re.search(r"Failed to (parse|read)", completed.stderr + completed.stdout)
    if completed.returncode not in (0, 1) and not unparsable:
        raise GateError(f"ruff format failed: {completed.stderr.strip()[-400:]}")
    found: list[Diagnostic] = []
    for line in completed.stdout.splitlines():
        if line.startswith("Would reformat: "):
            relpath = line.removeprefix("Would reformat: ").strip()
            found.append(
                Diagnostic(
                    "FORMAT",
                    relpath,
                    1,
                    "file is not formatted (run: ruff format)",
                    source="ruff-format",
                )
            )
    return found


def _python_executable(root: Path) -> str:
    project = root / ".venv" / "bin" / "python"
    return str(project) if project.exists() else (shutil.which("python3") or sys.executable)


def _mypy(root: Path, production: Sequence[SourceFile], result: GateResult) -> list[Diagnostic]:
    if not production:
        return []
    config = _home() / "config" / "mypy.ini"
    completed = _run(
        "mypy",
        [
            _tool("mypy"),
            "--config-file",
            str(config),
            "--python-executable",
            _python_executable(root),
            "--cache-dir=/dev/null",
            "--show-column-numbers",
            "--no-error-summary",
            "--no-pretty",
            "--no-color-output",
            *[source.relpath for source in production],
        ],
        root,
        result,
    )
    if completed.returncode not in (0, 1):
        raise GateError(f"mypy crashed: {(completed.stderr or completed.stdout).strip()[-400:]}")
    found: list[Diagnostic] = []
    for line in completed.stdout.splitlines():
        match = _MYPY_LINE.match(line)
        if match and match["sev"] == "error":
            code = f"MYPY.{match['code'] or 'error'}"
            found.append(
                Diagnostic(
                    code,
                    match["path"],
                    int(match["line"]),
                    match["msg"],
                    col=int(match["col"] or 1),
                    source="mypy",
                )
            )
    if completed.returncode == 1 and not found:
        raise GateError(f"mypy failed without diagnostics: {completed.stdout.strip()[-400:]}")
    return found


def _lock_parity(root: Path, result: GateResult) -> list[Diagnostic]:
    if not (root / "pyproject.toml").is_file():
        return []
    uv = shutil.which("uv")
    if uv is None:
        raise GateError("uv is not on PATH for the lockfile parity check")
    completed = _run("uv-lock-check", [uv, "lock", "--check", "--offline", "--quiet"], root, result)
    if completed.returncode == 0:
        return []
    detail = (completed.stderr or completed.stdout).strip().splitlines()
    reason = detail[-1] if detail else "uv lock --check failed"
    message = f"uv.lock is missing or out of date with pyproject.toml ({reason})"
    return [Diagnostic("PARITY.lock-version", "pyproject.toml", 1, message, source="uv")]


def _source_findings(sources: Sequence[SourceFile]) -> tuple[list[Diagnostic], set[str]]:
    """Bespoke findings over every source, and the files no tool can read."""
    found = structure_diagnostics(sources)
    unreadable: set[str] = set()
    for source in sources:
        per_file = source_diagnostics(source)
        unreadable.update(d.relpath for d in per_file if d.code in {"PYQ299", "PYS000"})
        found.extend(per_file)
    found.extend(fanout_diagnostics(sources))
    return found, unreadable


def _tool_findings(
    root: Path, sources: Sequence[SourceFile], production: Sequence[SourceFile], result: GateResult
) -> list[Diagnostic]:
    found: list[Diagnostic] = []
    if sources:
        found.extend(_ruff_check(root, result))
        found.extend(_ruff_format(root, result))
    found.extend(_mypy(root, production, result))
    found.extend(_lock_parity(root, result))
    return found


def _tests(root: Path, sources: Sequence[SourceFile], result: GateResult) -> list[Diagnostic]:
    """The tests check's findings; its outcome goes to the report."""
    try:
        found, outcome = tests_findings(root, sources, _home(), _python_executable(root))
    except TestsGateError as exc:
        raise GateError(str(exc)) from exc
    result.tests = outcome.as_dict()
    result.tools.append(ToolRun("tests", outcome.seconds, 0))
    return found


def run_gate(root: Path, *, mypy_enabled: bool = True, tests_enabled: bool = True) -> GateResult:
    """Run every check and attach a tier to each finding."""
    if not root.is_dir():
        raise GateError(f"workspace root does not exist: {root}")
    policy = load_policy()
    result = GateResult()
    sources = discover(root)
    production = [source for source in sources if not source.is_test]
    result.files_checked, result.production_files = len(sources), len(production)
    found, unreadable = _source_findings(sources)
    typed = [source for source in production if source.relpath not in unreadable]
    found.extend(_tool_findings(root, sources, typed if mypy_enabled else [], result))
    if tests_enabled:
        found.extend(_tests(root, sources, result))
    result.findings.extend(
        (diagnostic, tier)
        for diagnostic in found
        if (tier := tier_for(diagnostic.code, policy)) != "off"
    )
    return result


def _fix(root: Path) -> None:
    config = str(_home() / "config" / "ruff.toml")
    for argv in (
        [_tool("ruff"), "check", "--config", config, "--no-cache", "--fix", "--exit-zero", "."],
        [_tool("ruff"), "format", "--config", config, "--no-cache", "."],
    ):
        subprocess.run(argv, cwd=root, check=False, timeout=TOOL_TIMEOUT_SEC, capture_output=True)


def _print_section(title: str, items: Sequence[Diagnostic]) -> None:
    if not items:
        return
    print(f"{title} ({len(items)}):")
    for diagnostic in items[:PRINT_CAP]:
        print(f"  {diagnostic.render()}")
    if len(items) > PRINT_CAP:
        print(f"  ... {len(items) - PRINT_CAP} more (see the JSON report)")


def check_name(diagnostic: Diagnostic) -> str:
    """Return the gate-check name that produced a diagnostic."""
    if diagnostic.source != "bespoke":
        return diagnostic.source
    if diagnostic.code.startswith("FANOUT"):
        return "fanout"
    if diagnostic.code.startswith("PYQ1"):
        return "structure"
    return "source-rules"


def _json_path(args: argparse.Namespace) -> Path | None:
    if args.json_out:
        return Path(args.json_out)
    folder_name = args.json_dir or os.environ.get(REPORT_DIR_ENV)
    if not folder_name:
        return None
    folder = Path(folder_name)
    folder.mkdir(parents=True, exist_ok=True)
    index = 1
    while (folder / f"gate-{index}.json").exists():
        index += 1
    return folder / f"gate-{index}.json"


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def workspace_configs(root: Path) -> dict[str, dict[str, str | None]]:
    """Whether each workspace config copy still matches its protected config.

    The gate never reads the copies (it runs on the protected configs), so an
    edit cannot loosen it; the state is recorded in the JSON report only.
    """
    states: dict[str, dict[str, str | None]] = {}
    for name in WORKSPACE_CONFIGS:
        protected = _sha256(_home() / "config" / name)
        found = _sha256(root / name) if (root / name).is_file() else None
        state = "missing" if found is None else "identical" if found == protected else "modified"
        states[name] = {"state": state, "sha256": found, "protected_sha256": protected}
    return states


def _report(result: GateResult, verdict: str, error: str | None, root: Path) -> dict[str, object]:
    return {
        "schema": REPORT_SCHEMA,
        "pack": PACK,
        "verdict": verdict,
        "error": error,
        "files_checked": result.files_checked,
        "production_files": result.production_files,
        "counts": {
            tier: dict(Counter(d.code for d, t in result.findings if t == tier))
            for tier in ("block", "warn")
        },
        "findings": [{**d.as_dict(), "tier": t} for d, t in result.findings],
        "tools": [vars(tool) for tool in result.tools],
        "policy_sha256": _sha256(_home() / "config" / "policy.toml"),
        "workspace_configs": workspace_configs(root),
        "tests": result.tests,
    }


def _write_report(path: Path | None, report: dict[str, object]) -> None:
    """Write the JSON report; a failure is reported on stderr and never decides."""
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"guardrail-check: could not write the JSON report: {exc}", file=sys.stderr)


_TEST_TOOLS_VERSION = (
    "import coverage, pytest; "
    "print(f'pytest {pytest.__version__}, coverage {coverage.__version__}')"
)


def tool_versions() -> str:
    """``<pack>: ruff <v>, mypy <v>, pytest <v>, coverage <v>`` (raises GateError)."""
    found = []
    for argv in (
        [_tool("ruff"), "--version"],
        [_tool("mypy"), "--version"],
        [_tool("python"), "-I", "-c", _TEST_TOOLS_VERSION],
    ):
        completed = subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=TOOL_TIMEOUT_SEC
        )
        if completed.returncode != 0:
            raise GateError(f"{argv[0]} --version failed: {completed.stderr.strip()[-200:]}")
        found.append(completed.stdout.strip().split(" (")[0])
    return f"{PACK}: {', '.join(found)}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="guardrail-check", description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--json-dir", type=Path)
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--no-mypy", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-tests", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="store_true")
    return parser


def _decide(root: Path, args: argparse.Namespace) -> tuple[GateResult, str | None]:
    """The gate's result, or the error that kept it from deciding."""
    try:
        if args.fix:
            _fix(root)
        return run_gate(root, mypy_enabled=not args.no_mypy, tests_enabled=not args.no_tests), None
    except GateError as exc:
        return GateResult(), str(exc)
    except Exception as exc:  # the gate's boundary: any failure is a gate error, never a verdict
        return GateResult(), f"{type(exc).__name__}: {exc}"


def _report_path(args: argparse.Namespace) -> Path | None:
    try:
        return _json_path(args)
    except OSError as exc:
        print(f"guardrail-check: could not create the report dir: {exc}", file=sys.stderr)
        return None


def _print_verdict(result: GateResult, verdict: str) -> None:
    blocking = [d for d, t in result.findings if t == "block"]
    warnings = [d for d, t in result.findings if t == "warn"]
    print(
        f"guardrail-check: {verdict.upper()} ({len(blocking)} blocking, {len(warnings)} warnings, "
        f"{result.files_checked} Python files)"
    )
    failed = {check_name(d) for d in blocking}
    for name in CHECKS:
        print(f"gate-check {name} {'fail' if name in failed else 'pass'}")
    _print_section("Blocking (fix these; the change is refused until they pass)", blocking)
    _print_section("Warnings (not blocking)", warnings)
    if blocking:
        print("Re-run `guardrail-check` to confirm.", end=" ")
        print("`guardrail-check --fix` applies Ruff's safe fixes and formatting.")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the gate and return its exit code (0 pass, 1 refused, 126 gate error)."""
    args = _parser().parse_args(argv)
    if args.version:
        return _print_version()
    root = args.root.resolve()
    result, error = _decide(root, args)
    blocked = any(tier == "block" for _, tier in result.findings)
    verdict = "error" if error else "refused" if blocked else "passed"
    _write_report(_report_path(args), _report(result, verdict, error, root))
    if error:
        print(f"guardrail-check: ERROR, the gate could not run: {error}")
        return GATE_ERROR_EXIT
    _print_verdict(result, verdict)
    return 1 if blocked else 0


def _print_version() -> int:
    try:
        print(tool_versions())
    except (GateError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"guardrail-check: ERROR, the gate could not run: {exc}")
        return GATE_ERROR_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
