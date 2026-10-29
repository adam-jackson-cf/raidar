"""The fixture service behind ``stage-check`` and its trial replay (public contract: stage/1).

Everything runs in one throwaway offline pack container as UID 1000 through the same call path
a trial uses; package code never runs on the host. ``check_package`` validates the static
fixture contract, installs the package, runs every authored fixture twice (byte-identical
stdout and output digests required), then Raidar's adversarial data cases and bounds cases, and
returns the passing ``done`` canary record. ``replay_trial`` runs a trial's captured state
through the package again.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from raidar.subject.package.manifest import StagePackage
from raidar.subject.primitive.calls import (
    CallResult,
    CallRunner,
    ResourceFloors,
    TransportError,
    make_state_dir,
    resource_floors,
    root_command,
    trusted_exec,
    upload_dir,
)
from raidar.subject.primitive.canary import (
    CANARY_ROOT,
    WRITABLE_PATHS,
    FixtureRun,
    StageCheckRecord,
    run_fixture,
)
from raidar.subject.primitive.gate import GateInvalid, GateOutcome, GatePackage, GateRun
from raidar.subject.primitive.install import InstalledPackage, install_package
from raidar.subject.primitive.outputs import collect_outputs
from raidar.subject.primitive.site import PackageSite, TrialInvalid
from raidar.subject.workflow.identity import package_primitives
from raidar.subject.workflow.stage_cases import (
    GATE_STATUSES,
    Expectation,
    read_expectation,
    result_status,
    static_problems,
    stop_status,
)
from raidar.subject.workflow.stage_container import PackContainer

GATE_WORKSPACE = "/app"
OVERSIZED_BYTES = 70 * 1024 * 1024
_WEIRD_BYTES = b"\xff\xfe\x00\xc3(\x80"
_DEEP_JSON = "[" * 800 + "]" * 800
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class CaseResult:
    """One fixture or generated case: whether it passed and why not."""

    case: str
    kind: str
    ok: bool
    status: str | None = None
    problems: tuple[str, ...] = ()


@dataclass
class CheckReport:
    """The outcome of ``check_package``; ``record`` is set only when every case passed."""

    reference: str
    digest: str
    pack: str
    primitives: dict[str, str]
    cases: list[CaseResult] = field(default_factory=list)
    static: list[str] = field(default_factory=list)
    record: StageCheckRecord | None = None
    installed: dict[str, Any] | None = None
    """The ``install/1`` record: architecture, installed-tree digest and wheel inventory."""

    @property
    def ok(self) -> bool:
        return not self.static and all(item.ok for item in self.cases) and bool(self.cases)


def _reference_key(reference: str) -> str:
    return _SAFE.sub("_", reference)


def _site(
    container: PackContainer,
    logs_dir: Path,
    package: StagePackage,
    reference: str,
    installed: InstalledPackage,
) -> PackageSite:
    return PackageSite(
        environment=container,
        runner=CallRunner(container, logs_dir),
        logs_dir=logs_dir,
        package=package,
        reference=reference,
        installed=installed,
        task="fixture",
        params=package.effective_params(),
    )


def _same(first: FixtureRun, second: FixtureRun) -> bool:
    return first.stdout_sha256 == second.stdout_sha256 and first.outputs == second.outputs


def _timing(site: PackageSite, run: FixtureRun) -> str | None:
    stop = site.package.manifest.on_stop
    assert stop is not None
    duration = run.record.get("duration_s")
    if isinstance(duration, int | float) and duration > stop.timeout_s / 4:
        return f"took {duration:.1f}s, over a quarter of the {stop.timeout_s}s timeout"
    return None


async def _stop_case(site: PackageSite, case: str, expected: Expectation) -> CaseResult:
    first = await run_fixture(site, case, f"{case}-0")
    second = await run_fixture(site, case, f"{case}-1")
    problems: list[str] = []
    status, why = stop_status(first, site.package.manifest.tokens)
    if why:
        problems.append(why)
    elif status != expected.status:
        problems.append(f"expected status {expected.status}, got {status}")
    if not _same(first, second):
        problems.append("two runs differ in stdout or output digests")
    for name, digest in expected.outputs.items():
        if first.outputs.get(name) != digest:
            problems.append(f"output {name}: expected {digest}, got {first.outputs.get(name)}")
    return CaseResult(case, "fixture", not problems, status, tuple(problems))


@dataclass(frozen=True)
class Rig:
    """Where a fresh throwaway container comes from (gate fixtures each need their own)."""

    image: str
    docker_context: str
    logs_dir: Path
    cache_root: Path | None


def _gate_key(outcome: GateOutcome, canary: Mapping[str, Any] | None) -> dict[str, Any]:
    """What two runs of a gate fixture must agree on: attribution, verdicts and every check."""
    record = outcome.record
    return {
        "attribution": outcome.attribution.value,
        "canary": None if canary is None else canary.get("verdict"),
        "verdict": record.get("verdict"),
        "checks": [
            [item.get("name"), item.get("status"), item.get("exit")]
            for item in record.get("checks", [])
        ],
    }


async def _gate_once(
    package: StagePackage, reference: str, case: str, label: str, rig: Rig
) -> dict[str, Any]:
    """One fresh container: the fixture's state is the workspace; the gate's own starting canary
    (setup check and a run over the unchanged workspace) must decide, then the seal runs."""
    context = json.loads((package.fixture_dir(case) / "context.json").read_text(encoding="utf-8"))
    logs = rig.logs_dir / f"gate-{label}"
    async with PackContainer(rig.image, rig.docker_context) as container:
        installed = await _install(container, logs, package, reference, rig.cache_root)
        state = package.fixture_dir(case) / "state"
        if state.is_dir():
            await upload_dir(container, state, GATE_WORKSPACE)
        await root_command(container, f"chown -R 1000:1000 {GATE_WORKSPACE}")
        gate = GateRun(
            container,
            logs,
            [
                GatePackage(
                    package.name,
                    installed,
                    package.manifest.gate,
                    context,
                    outputs=package.manifest.outputs,
                )
            ],
        )
        try:
            await gate.prepare()
            outcome = await gate.seal(f"fixture-{label}")
            copied = await collect_outputs(
                _site(container, logs, package, reference, installed), f"gate-{label}"
            )
        except GateInvalid as error:
            raise TrialInvalid(error.category, str(error)) from error
        finally:
            await gate.close()
        key = _gate_key(outcome, gate.evidence.canary)
    key["outputs"] = copied.digests()
    key["output_errors"] = copied.problems
    key["reason"] = outcome.reason
    return key


def _decision_problems(first: dict[str, Any], expected: Expectation) -> list[str]:
    outcome = _outcome(first)
    if outcome is None:
        return [f"the gate could not decide: {first['attribution']}: {first['reason']}"]
    if outcome != expected.status:
        return [f"expected {expected.status}, got {outcome}"]
    return []


def _outcome(observation: dict[str, Any]) -> str | None:
    return {"passed": "pass", "refused": "refuse"}.get(observation["attribution"])


def _coverage_problems(package: StagePackage, first: dict[str, Any]) -> list[str]:
    """On a pass every declared check passed; on any run no check errored."""
    seen = {name: status for name, status, *_ in first["checks"]}
    problems: list[str] = []
    if _outcome(first) == "pass":
        declared = sorted(f"{package.name}.{check.name}" for check in package.manifest.gate)
        problems.extend(
            f"check {name} did not pass (status {seen.get(name)})"
            for name in declared
            if seen.get(name) != "passed"
        )
    if "error" in seen.values():
        problems.append("a check errored instead of deciding")
    return problems


def _stable(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Agree on decisions, statuses, exits and output digests, not the free-text reason."""
    return {k: v for k, v in first.items() if k != "reason"} == {
        k: v for k, v in second.items() if k != "reason"
    }


def _output_problems(expected: Expectation, first: dict[str, Any]) -> list[str]:
    """Every expected output digest must be the one the sealed gate fixture left."""
    problems: list[str] = []
    for name, digest in sorted(expected.outputs.items()):
        if name in first["output_errors"]:
            problems.append(f"output {name} is invalid: {first['output_errors'][name]}")
        elif first["outputs"].get(name) != digest:
            problems.append(f"output {name}: expected {digest}, got {first['outputs'].get(name)}")
    return problems


async def _gate_case(
    package: StagePackage, reference: str, case: str, expected: Expectation, rig: Rig
) -> CaseResult:
    first = await _gate_once(package, reference, case, f"{case}-0", rig)
    second = await _gate_once(package, reference, case, f"{case}-1", rig)
    problems = _decision_problems(first, expected) + _coverage_problems(package, first)
    problems += _output_problems(expected, first)
    if not _stable(first, second):
        problems.append("two runs differ in attribution, verdict, check results or outputs")
    return CaseResult(case, "fixture", not problems, _outcome(first), tuple(problems))


def _state_files(done: Path) -> list[Path]:
    return [item for item in sorted(done.rglob("*")) if item.is_file()] if done.is_dir() else []


def _damage(out: Path, mode: str) -> None:
    """Overwrite one state file as ``mode`` damages it."""
    writers = {
        "zero-byte": lambda: out.write_bytes(b""),
        "invalid-json": lambda: out.write_text("{not json", encoding="utf-8"),
        "non-utf8": lambda: out.write_bytes(_WEIRD_BYTES),
        "deep-json": lambda: out.write_text(_DEEP_JSON, encoding="utf-8"),
        "wrong-state": lambda: out.write_text("unrelated words, no check\n", encoding="utf-8"),
    }
    if mode in writers:
        writers[mode]()


def _mutated(done: Path, target: Path, mode: str) -> None:
    """Write ``target`` as the ``done`` state altered by ``mode`` (an adversarial data case)."""
    target.mkdir(parents=True)
    for item in _state_files(done):
        out = target / item.relative_to(done)
        out.parent.mkdir(parents=True, exist_ok=True)
        _damage(out, mode)
    if mode == "empty-state":
        shutil.rmtree(target)
        target.mkdir()


DATA_CASES = ("empty-state", "zero-byte", "invalid-json", "non-utf8", "deep-json", "wrong-state")


def _with_generated(package: StagePackage, scratch: Path) -> StagePackage:
    """A copy of the package whose fixtures also hold Raidar's data cases (read as data only)."""
    root = scratch / package.root.name
    shutil.copytree(package.root, root, symlinks=True)
    done = package.fixture_dir("done")
    context = (done / "context.json").read_text(encoding="utf-8")
    for mode in DATA_CASES:
        case = root / "fixtures" / f"raidar-{mode}"
        _mutated(done / "state", case / "state", mode)
        (case / "context.json").write_text(context, encoding="utf-8")
    return replace(package, root=root)


async def _data_case(site: PackageSite, mode: str) -> CaseResult:
    case = f"raidar-{mode}"
    first = await run_fixture(site, case, f"{case}-0")
    second = await run_fixture(site, case, f"{case}-1")
    problems: list[str] = []
    status, why = stop_status(first, site.package.manifest.tokens)
    if why:
        problems.append(why)
    elif status == "done":
        problems.append("a damaged state must not report done")
    timing = _timing(site, first)
    if timing:
        problems.append(timing)
    if not _same(first, second):
        problems.append("two runs differ in stdout or output digests")
    return CaseResult(case, "adversarial", not problems, status, tuple(problems))


async def _special_state(container: PackContainer, mode: str, root: str) -> str:
    state = f"{root}/state"
    await make_state_dir(container, state)
    commands = {
        "fifo": f"mkfifo {state}/pipe",
        "symlink-loop": f"ln -s loop {state}/loop",
        "oversized": f"truncate -s {OVERSIZED_BYTES} {state}/big.bin",
        "unreadable": f"echo data > {state}/secret && chmod 000 {state}/secret",
    }
    result = await trusted_exec(container, commands[mode], user=1000, timeout_sec=60)
    if result.return_code != 0:
        raise TransportError(f"could not build {mode} state: {result.stderr}")
    return state


async def _run_call(site: PackageSite, state: str) -> CallResult:
    stop = site.package.manifest.on_stop
    assert stop is not None
    context = dict(site.context(round_=0, carry={}))
    context["state"] = state
    return await site.runner.run(
        site.reference,
        "stop/1",
        stop.run,
        context,
        timeout_s=stop.timeout_s,
        path=site.installed.path,
        installed=[site.installed],
        state=state,
    )


async def _bounds_case(site: PackageSite, container: PackContainer, mode: str) -> CaseResult:
    root = f"{CANARY_ROOT}/{site.scope}-{site.evidence_dir.name}/raidar-{mode}"
    state = await _special_state(container, mode, root)
    result = await _run_call(site, state)
    problems: list[str] = []
    status: str | None = None
    if mode == "unreadable":
        if result.integrity_error:
            problems.append(result.integrity_error)
        elif result.bounds_violation:
            problems.append(f"unreadable file was a bounds violation: {result.bounds_violation}")
        elif site.package.manifest.on_stop is not None:
            try:
                status = result_status(result.stdout, site.package.manifest.tokens)
            except ValueError as error:
                problems.append(str(error))
            if status == "done":
                problems.append("an unreadable state must not report done")
    else:
        status = "bounds"
        if not result.bounds_violation:
            problems.append("watched state outside Raidar's bounds was not rejected")
        if result.ran:
            problems.append("authored code ran against state outside Raidar's bounds")
    return CaseResult(f"raidar-{mode}", "bounds", not problems, status, tuple(problems))


def _floors(before: ResourceFloors, after: ResourceFloors) -> tuple[int, int | None]:
    """The recorded floors: the least agent-writable disk and available memory seen around the
    passing ``done`` run (``ResourceFloors`` semantics). The canary demonstrably succeeded with at
    least this much free, so a failed canary with less is the environment's, not the tool's."""
    disks = [
        free for floors in (before, after) for free in floors.disk_free.values() if free is not None
    ]
    memory = [m for m in (before.memory_available, after.memory_available) if m is not None]
    return min(disks, default=0), min(memory, default=None)


BOUNDS_MODES = ("fifo", "symlink-loop", "oversized", "unreadable")


async def _fixture_cases(
    package: StagePackage, reference: str, site: PackageSite, rig: Rig
) -> list[CaseResult]:
    """Every authored fixture, run as the gate or the stop its expected status names."""
    results: list[CaseResult] = []
    for case in package.fixture_cases():
        expected = read_expectation(package.fixture_dir(case) / "expect.yaml")
        if expected.status in GATE_STATUSES:
            results.append(await _gate_case(package, reference, case, expected, rig))
        else:
            results.append(await _stop_case(site, case, expected))
    return results


async def _stop_only_cases(site: PackageSite, container: PackContainer) -> list[CaseResult]:
    """Raidar's data cases and, for watched state, its bounds cases."""
    results = [await _data_case(site, mode) for mode in DATA_CASES]
    if site.watch is not None:
        results.extend([await _bounds_case(site, container, mode) for mode in BOUNDS_MODES])
    return results


async def check_package(
    package: StagePackage,
    reference: str,
    *,
    image: str,
    docker_context: str,
    pack: str,
    logs_dir: Path,
    cache_root: Path | None = None,
) -> CheckReport:
    """Check ``package`` (``reference`` is ``<ns>/<name>``) offline in a pack container."""
    primitives = package_primitives([package])
    report = CheckReport(reference, package.digest, pack, primitives)
    report.static = static_problems(package)
    if report.static:
        return report
    manifest = package.manifest
    rig = Rig(image, docker_context, logs_dir, cache_root)
    with tempfile.TemporaryDirectory(prefix="raidar-stage-check-") as scratch:
        checked = _with_generated(package, Path(scratch)) if manifest.on_stop else package
        async with PackContainer(image, docker_context) as container:
            installed = await _install(container, logs_dir, package, reference, cache_root)
            report.installed = dict(installed.record)
            site = _site(container, logs_dir, checked, reference, installed)
            report.cases.extend(await _fixture_cases(package, reference, site, rig))
            if manifest.on_stop is not None:
                report.cases.extend(await _stop_only_cases(site, container))
            tree = await installed.verify()
            if not tree.ok:
                report.cases.append(
                    CaseResult("installed-tree", "integrity", False, None, ("tool bytes changed",))
                )
            if report.ok and manifest.on_stop is not None:
                report.record = await _record(site, container, package, pack, primitives)
    return report


async def _install(
    container: PackContainer,
    logs_dir: Path,
    package: StagePackage,
    reference: str,
    cache_root: Path | None,
) -> InstalledPackage:
    tool = package.manifest.tool
    return await install_package(
        container,
        logs_dir,
        reference=reference,
        package_digest=package.digest,
        files=package.files,
        commands={} if tool is None else {k: list(v) for k, v in tool.commands.items()},
        cache_root=cache_root,
    )


async def _record(
    site: PackageSite,
    container: PackContainer,
    package: StagePackage,
    pack: str,
    primitives: Mapping[str, str],
) -> StageCheckRecord:
    before = await resource_floors(container, WRITABLE_PATHS)
    done = await run_fixture(site, "done", "done-record")
    after = await resource_floors(container, WRITABLE_PATHS)
    missing = [name for name, digest in done.outputs.items() if digest is None]
    if missing:
        raise TrialInvalid("instrument", f"the done fixture left no output {missing}")
    disk, memory = _floors(before, after)
    return StageCheckRecord(
        package=package.digest,
        pack=pack,
        primitives=dict(primitives),
        stdout_sha256=done.stdout_sha256,
        outputs={name: str(digest) for name, digest in done.outputs.items()},
        disk_free=disk,
        memory_available=memory,
    )


@dataclass(frozen=True)
class ReplayRun:
    """One captured trial state run through the package again."""

    snapshot: str
    status: str | None
    problem: str | None
    stdout_sha256: str


@dataclass(frozen=True)
class CapturedCall:
    """A trial's pre-call state snapshot and the context file recorded with it (if any)."""

    snapshot: Path
    context: Path | None


def captured_states(trial_dir: Path, reference: str) -> list[CapturedCall]:
    """Stop-call snapshots (``stop-N-before``) a trial kept for ``reference``, each with the
    ``stop-N-context.json`` the trial recorded beside it."""
    key = _reference_key(reference)
    found = sorted(trial_dir.glob(f"**/stages/{key}/stop-*-before"))
    calls_found = []
    for item in found:
        if item.is_dir():
            context = item.with_name(item.name.removesuffix("-before") + "-context.json")
            calls_found.append(CapturedCall(item, context if context.is_file() else None))
    return calls_found


async def replay_trial(
    package: StagePackage,
    reference: str,
    captured: list[CapturedCall],
    *,
    image: str,
    docker_context: str,
    logs_dir: Path,
    cache_root: Path | None = None,
) -> list[ReplayRun]:
    """Run each captured call again with its recorded context (params, round, carry, task).

    A snapshot without a recorded context is refused, never replayed with a made-up one.
    """
    if package.manifest.on_stop is None:
        raise ValueError(f"{reference} declares no stop to replay")
    usable = [item for item in captured if item.context is not None]
    runs = [
        ReplayRun(str(item.snapshot), None, "refused: no recorded call context for this state", "")
        for item in captured
        if item.context is None
    ]
    if not usable:
        return runs
    with tempfile.TemporaryDirectory(prefix="raidar-stage-replay-") as scratch:
        root = Path(scratch) / package.root.name
        shutil.copytree(package.root, root, symlinks=True)
        for index, item in enumerate(usable):
            assert item.context is not None
            case = root / "fixtures" / f"replay-{index}"
            case.mkdir(parents=True)
            shutil.copytree(item.snapshot, case / "state", symlinks=True)
            shutil.copyfile(item.context, case / "context.json")
        replayed = replace(package, root=root)
        async with PackContainer(image, docker_context) as container:
            installed = await _install(container, logs_dir, package, reference, cache_root)
            site = _site(container, logs_dir, replayed, reference, installed)
            for index, item in enumerate(usable):
                run = await run_fixture(site, f"replay-{index}", f"replay-{index}")
                status, why = stop_status(run, package.manifest.tokens)
                runs.append(ReplayRun(str(item.snapshot), status, why, run.stdout_sha256))
    return runs


def record_json(record: StageCheckRecord) -> dict[str, Any]:
    """The stored JSON form of a passing record."""
    return dict(record.as_json())
