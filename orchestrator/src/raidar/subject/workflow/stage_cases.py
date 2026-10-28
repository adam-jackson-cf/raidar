"""The fixture contract of a stage package as data (public contract: stage/1).

What each fixture expects (``expect.yaml``), which fixtures a package's shape makes mandatory,
the static problems found before any container exists, and the status of a stop fixture's
result. The container service in ``stage_check`` runs the cases; this module only judges data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from raidar.subject.package.manifest import StagePackage
from raidar.subject.primitive.canary import FixtureRun
from raidar.subject.primitive.stop import parse_result

STOP_STATUSES = ("done", "resume", "closed")
GATE_STATUSES = ("pass", "refuse")
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Expectation:
    """A fixture's ``expect.yaml``: its status and the digest of each output it expects."""

    status: str
    outputs: dict[str, str]


def _valid_outputs(outputs: object) -> bool:
    if not isinstance(outputs, dict):
        return False
    return all(
        isinstance(key, str) and isinstance(value, str) and _SHA256.fullmatch(value)
        for key, value in outputs.items()
    )


def read_expectation(path: Path) -> Expectation:
    """The expectation in ``path``; ``ValueError`` names what is refused."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"expect.yaml is not valid YAML: {error}") from error
    if not isinstance(data, dict) or set(data) - {"status", "outputs"} or "status" not in data:
        raise ValueError("expect.yaml is a mapping of exactly status and optional outputs")
    if data["status"] not in (*STOP_STATUSES, *GATE_STATUSES):
        allowed = ", ".join((*STOP_STATUSES, *GATE_STATUSES))
        raise ValueError(f"status {data['status']!r} is not one of {allowed}")
    outputs = data.get("outputs") or {}
    if not _valid_outputs(outputs):
        raise ValueError("expect.yaml outputs map output names to sha256 hex digests")
    return Expectation(str(data["status"]), dict(outputs))


def required_cases(package: StagePackage) -> tuple[str, ...]:
    """The fixture cases the contract makes mandatory for this package's shape."""
    stop = package.manifest.on_stop
    cases: tuple[str, ...] = ()
    if stop is not None:
        cases = ("done", "closed", *(("resume",) if stop.max_rounds > 0 else ()))
    if package.manifest.gate:
        cases = (*cases, *GATE_STATUSES)
    return cases


def _context_problem(folder: Path) -> str | None:
    """Why ``context.json`` is not a JSON object (``None``: it is one)."""
    data = json.loads((folder / "context.json").read_text(encoding="utf-8"))
    return None if isinstance(data, dict) else "context.json is not a JSON object"


def _expectation_problems(
    package: StagePackage, case: str, expected: Expectation, mandatory: tuple[str, ...]
) -> list[str]:
    manifest = package.manifest
    problems: list[str] = []
    if expected.status in GATE_STATUSES and not manifest.gate:
        problems.append(f"fixture {case!r} expects a gate result but the package has no gate")
    if expected.status in STOP_STATUSES and manifest.on_stop is None:
        problems.append(f"fixture {case!r} expects a stop result but the package has no stop")
    if case in mandatory and expected.status != case:
        problems.append(f"fixture {case!r} must expect status {case!r}")
    if case == "done" and expected.outputs.keys() != manifest.outputs.keys():
        problems.append("fixture 'done' must declare a digest for every output")
    undeclared = sorted(expected.outputs.keys() - manifest.outputs.keys())
    if undeclared:
        problems.append(f"fixture {case!r} expects undeclared outputs {undeclared}")
    return problems


def _fixture_problems(package: StagePackage, case: str, mandatory: tuple[str, ...]) -> list[str]:
    folder = package.fixture_dir(case)
    try:
        bad_context = _context_problem(folder)
        expected = read_expectation(folder / "expect.yaml")
    except (OSError, ValueError) as error:
        return [f"fixture {case!r}: {error}"]
    if bad_context:
        return [f"fixture {case!r}: {bad_context}"]
    return _expectation_problems(package, case, expected, mandatory)


def static_problems(package: StagePackage) -> list[str]:
    """Why the package's fixtures break the contract before any container exists."""
    problems: list[str] = []
    manifest = package.manifest
    if manifest.on_stop is None and not manifest.gate:
        problems.append("a package with neither on_stop nor a gate has nothing to check")
    cases = package.fixture_cases()
    mandatory = required_cases(package)
    problems.extend(
        f"missing mandatory fixture {case!r}" for case in mandatory if case not in cases
    )
    for case in cases:
        problems.extend(_fixture_problems(package, case, mandatory))
    return problems


def result_status(stdout: bytes, tokens: tuple[str, ...]) -> str:
    """The status of a stop result's ``stdout``; ``ValueError`` names what makes it unusable."""
    return parse_result(stdout, tokens).status


def stop_status(run: FixtureRun, tokens: tuple[str, ...]) -> tuple[str | None, str | None]:
    """(status, why not): the parsed stop status of a fixture call."""
    if not run.usable:
        return None, f"call unusable (exit {run.record.get('exit_code')})"
    stdout = str(run.record.get("stdout", "")).encode()
    try:
        return result_status(stdout, tokens), None
    except ValueError as error:
        return None, str(error)
