"""``declared-wheels/1`` resolution decisions: classified refusals, caps, outages, pins.

``uv`` is an injected scripted shell, so no test starts a process, sleeps, or reaches the
network. The real-``uv`` seam lives in :mod:`tests.test_dependency_resolver_slow`.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import urllib.error
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement

from raidar.contract import wheel_cache
from raidar.contract.wheels import PLATFORMS, PYTHON, DependencyInfraError
from raidar.design.problem import dependencies as problem_dependencies
from raidar.design.problem.dependencies import (
    ProblemDependencies,
    resolve_dependencies,
    write_locks,
)
from raidar.design.problem.models import (
    PackBinding,
    ProblemId,
    ProblemManifest,
    ProblemMode,
    ProblemSource,
    ProblemStep,
)
from raidar.design.problem.store import ProblemStore
from raidar.grading import resolver as resolver_module
from raidar.grading.dependency_pins import (
    DependencyLockError,
    DependencyLocks,
    DependencyPins,
    DependencyRefusal,
    lock_sha256,
    set_sha256,
)
from raidar.grading.resolver import (
    DependencyUnresolvable,
    DirectoryIndex,
    IndexFile,
    SimpleIndex,
    UvResolver,
    classify_failure,
    lock_sources_ok,
    pylock_packages,
    resolve_pins,
)
from tests.dependency_support import CUTOFF, FakeResolver, fake_lock, make_sdist, make_wheel


@pytest.fixture(autouse=True)
def local_wheel_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture locks name ``file://`` wheels; production accepts only the index's own host."""
    monkeypatch.setattr(wheel_cache, "WHEEL_URL_PREFIXES", ("file://",))


class _Index:
    def __init__(self, projects: dict[str, list[IndexFile]]) -> None:
        self.projects = projects

    def files(self, name: str) -> list[IndexFile] | None:
        return self.projects.get(name)


def test_direct_references_and_unparseable_lines_are_refused_without_running_uv() -> None:
    def never(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("uv must not run for refused lines")

    resolver = UvResolver(run=never)
    lines = [
        "pkg @ https://example.invalid/pkg-1.0-py3-none-any.whl",
        "not a requirement!!",
        "-e .",
        "-e git+https://example.invalid/x.git#egg=x",
        "git+https://example.invalid/x.git",
        "./local/pkg",
        "/abs/pkg.whl",
        "-r other.txt",
        "pyproject.toml (unreadable declaration)",
    ]
    result = resolver(lines, arch="amd64", exclude_newer=CUTOFF)
    reasons = {u.requirement: u.reason for u in result.unprovided}
    assert reasons == {
        lines[0]: "direct-reference",
        lines[1]: "unparseable",
        lines[2]: "direct-reference",
        lines[3]: "direct-reference",
        lines[4]: "direct-reference",
        lines[5]: "direct-reference",
        lines[6]: "direct-reference",
        lines[7]: "unparseable",
        lines[8]: "unparseable",
    }
    assert result.lock_text == ""
    assert result.provided == ()


@pytest.mark.parametrize(
    ("extra", "ok"),
    [
        ("", True),
        ('vcs = { url = "https://x/y.git" }\n', False),
        ('directory = { path = "." }\n', False),
        ('sdist = { url = "https://files.pythonhosted.org/p.tar.gz" }\n', False),
    ],
)
def test_lock_sources_must_be_index_wheels(extra: str, ok: bool) -> None:
    lock = fake_lock({"p": "1"}).replace('version = "1"\n', 'version = "1"\n' + extra)
    host = "https://files.example/"
    assert lock_sources_ok(lock, (host,)) is ok
    assert lock_sources_ok(lock, ("https://other.example/",)) is False
    assert lock_sources_ok('[[packages]]\nname = "p"\nversion = "1"\n', (host,)) is False


def test_the_byte_cap_counts_every_wheel_a_fetch_would_download() -> None:
    lock = fake_lock({"pkg": "1", "dep": "2"})  # two wheels of 10 bytes each
    host = ("https://files.example/",)
    over = UvResolver(max_bytes=19, wheel_url_prefixes=host)._within_caps(lock, ["pkg"])
    assert over[0] == ""
    assert [u.reason for u in over[1]] == ["cap-exceeded"]
    within = UvResolver(max_bytes=20, wheel_url_prefixes=host)._within_caps(lock, ["pkg"])
    assert within == (lock, [])


Outcome = subprocess.CompletedProcess[str] | Exception
Responder = Callable[[tuple[str, ...]], Outcome]


def _done(code: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, stderr)


def _timed_out() -> subprocess.TimeoutExpired:
    return subprocess.TimeoutExpired("uv", 1.0)


@dataclass
class ScriptedUv:
    """An injected shell: answers each ``uv`` call from the requirement lines it was given.

    ``respond`` returns the process outcome, or an exception for the shell to raise. A timeout
    advances ``clock`` by the budget the call was given, so no test ever sleeps.
    """

    respond: Responder
    clock: list[float] = field(default_factory=lambda: [0.0])
    requests: list[tuple[str, ...]] = field(default_factory=list)
    argvs: list[list[str]] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)

    def __call__(
        self, argv: list[str], *, timeout: float, **options: object
    ) -> subprocess.CompletedProcess[str]:
        lines = tuple(Path(argv[3]).read_text().splitlines()) if argv[1] == "pip" else tuple(argv)
        self.argvs.append(argv)
        self.requests.append(lines)
        self.timeouts.append(timeout)
        outcome = self.respond(lines)
        if isinstance(outcome, subprocess.TimeoutExpired):
            self.clock[0] += timeout
        if isinstance(outcome, Exception):
            raise outcome
        if options.get("check") and outcome.returncode:
            raise subprocess.CalledProcessError(outcome.returncode, argv, outcome.stdout)
        return outcome


def _answers(*outcomes: Outcome) -> Responder:
    queue = iter(outcomes)
    return lambda lines: next(queue)


def _resolves(*, refuse: Collection[str] = (), conflict: Collection[str] = ()) -> Responder:
    """A ``uv`` that solves every requirement to 1.0 but the refused and the conflicting."""

    def respond(lines: tuple[str, ...]) -> Outcome:
        if any(line in refuse for line in lines) or (conflict and set(conflict) <= set(lines)):
            return _done(1, stderr="No solution found when resolving dependencies")
        names = {Requirement(line).name: "1.0" for line in lines}
        return _done(stdout=fake_lock(names))

    return respond


@pytest.fixture(name="clock")
def clock_fixture(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [0.0]
    monkeypatch.setattr(resolver_module.time, "monotonic", lambda: now[0])
    return now


def scripted(uv: ScriptedUv, **options: Any) -> UvResolver:
    options = {"canary": "fast==1.0", "wheel_url_prefixes": ("https://files.example/",), **options}
    return UvResolver(run=uv, **options)


def _flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_uv_is_asked_for_wheels_only_under_the_cutoff_for_the_requested_architecture() -> None:
    uv = ScriptedUv(_resolves())
    result = scripted(uv)(["pkg>=1"], arch="arm64", exclude_newer=CUTOFF)
    assert uv.requests == [("pkg>=1",)]
    [argv] = uv.argvs
    assert _flag(argv, "--only-binary") == ":all:"
    assert _flag(argv, "--exclude-newer") == CUTOFF
    assert _flag(argv, "--python-platform") == PLATFORMS["arm64"]
    assert _flag(argv, "--python-version") == PYTHON
    assert result.lock_text == fake_lock({"pkg": "1.0"})
    assert result.provided == ("pkg==1.0",)
    assert (result.attempts, result.packages, result.wheel_bytes) == (1, 1, 10)
    assert not result.unprovided


def test_the_resolver_identity_is_the_uv_version_and_is_asked_once() -> None:
    uv = ScriptedUv(_answers(_done(stdout="uv 9.8.7 (abc 2026-01-01)\n")))
    resolver = UvResolver(run=uv)
    assert resolver.identity == "uv@9.8.7"
    assert resolver.identity == "uv@9.8.7"
    assert len(uv.requests) == 1


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        (_done(2, stderr="broken"), "uv is unavailable"),
        (FileNotFoundError("no uv"), "uv is unavailable"),
        (_timed_out(), "uv is unavailable"),
        (_done(stdout=""), "no version"),
        (_done(stdout="uv\n"), "no version"),
    ],
    ids=["nonzero", "missing", "timeout", "empty-output", "malformed-output"],
)
def test_an_unusable_uv_is_an_infrastructure_failure_for_its_identity(
    outcome: Outcome, message: str
) -> None:
    resolver = UvResolver(run=ScriptedUv(_answers(outcome)))
    with pytest.raises(DependencyInfraError, match=message):
        _ = resolver.identity


def test_a_missing_uv_during_resolution_is_an_infrastructure_failure() -> None:
    uv = ScriptedUv(_answers(FileNotFoundError("no uv")))
    with pytest.raises(DependencyInfraError, match="uv is unavailable"):
        scripted(uv)(["pkg"], arch="amd64", exclude_newer=CUTOFF)


def test_timeouts_are_retried_up_to_the_bound_then_fail_as_infrastructure(
    clock: list[float],
) -> None:
    uv = ScriptedUv(lambda lines: _timed_out(), clock)
    resolver = scripted(uv, timeout_retries=2)
    with pytest.raises(DependencyInfraError, match=r"timed out in 3 attempt.*retry bound: 2"):
        resolver(["slow"], arch="amd64", exclude_newer=CUTOFF)
    assert len(uv.timeouts) == 3
    assert all(call <= resolver.timeout_s for call in uv.timeouts)


def test_production_resolver_makes_at_most_two_attempts_then_reports_infrastructure(
    clock: list[float],
) -> None:
    uv = ScriptedUv(lambda lines: _timed_out(), clock)
    resolver = scripted(uv)
    assert UvResolver().timeout_retries == 1
    assert UvResolver().timeout_s == 120.0
    with pytest.raises(DependencyInfraError, match=r"timed out in 2 attempt.*120 s each"):
        resolver(["slow"], arch="amd64", exclude_newer=CUTOFF)
    assert len(uv.timeouts) == 2
    assert all(call <= 120.0 for call in uv.timeouts)


def test_one_timeout_is_recovered_by_an_identical_retry(clock: list[float]) -> None:
    lock = fake_lock({"slow": "1.0"})
    uv = ScriptedUv(_answers(_timed_out(), _done(stdout=lock)), clock)
    result = scripted(uv)(["slow"], arch="amd64", exclude_newer=CUTOFF)
    assert result.attempts == 2
    assert not result.unprovided
    assert result.lock_text == lock
    assert uv.requests[0] == uv.requests[1]
    first, second = uv.argvs
    assert first[:3] + first[4:] == second[:3] + second[4:]
    assert "--exclude-newer" in first


def test_an_unreachable_index_is_an_infrastructure_failure_never_unprovided() -> None:
    stderr = "error sending request for url (https://pypi.org/simple/x/)"
    uv = ScriptedUv(_answers(_done(2, stderr=stderr)))
    with pytest.raises(DependencyInfraError, match="unavailable"):
        scripted(uv)(["pkg"], arch="amd64", exclude_newer=CUTOFF)
    assert uv.requests == [("pkg",)]  # the network signature needs no canary


def test_a_failure_the_canary_shares_is_infrastructure_and_one_it_does_not_is_a_verdict(
    clock: list[float],
) -> None:
    broken = ScriptedUv(_resolves(refuse={"pkg", "fast==1.0"}))
    with pytest.raises(DependencyInfraError, match="unavailable"):
        scripted(broken, index=_Index({}))(["pkg"], arch="amd64", exclude_newer=CUTOFF)
    assert broken.requests == [("pkg",), ("fast==1.0",)]
    hung = ScriptedUv(
        lambda lines: _timed_out() if lines == ("fast==1.0",) else _done(1, stderr="No solution"),
        clock,
    )
    with pytest.raises(DependencyInfraError, match="unavailable"):
        scripted(hung, index=_Index({}))(["pkg"], arch="amd64", exclude_newer=CUTOFF)
    healthy = ScriptedUv(_resolves(refuse={"pkg"}))
    result = scripted(healthy, index=_Index({}))(["pkg"], arch="amd64", exclude_newer=CUTOFF)
    assert [(u.requirement, u.reason) for u in result.unprovided] == [("pkg", "unresolvable")]


def test_each_failed_requirement_is_classified_alone_and_the_survivors_are_still_provided() -> None:
    index = _Index(
        {
            "solo": [IndexFile("solo-1.0.tar.gz", EARLY)],
            "late": [IndexFile("late-2.0-py3-none-any.whl", LATE)],
        }
    )
    uv = ScriptedUv(_resolves(refuse={"solo", "late>=2"}))
    declared = ["good", "solo", "late>=2"]
    result = scripted(uv, index=index)(declared, arch="amd64", exclude_newer=CUTOFF)
    assert sorted((u.requirement, u.reason) for u in result.unprovided) == [
        ("late>=2", "after-cutoff"),
        ("solo", "no-wheel"),
    ]
    assert result.provided == ("good==1.0",)
    assert pylock_packages(result.lock_text) == [("good", "1.0")]
    assert uv.requests[0] == ("good", "solo", "late>=2")
    assert uv.requests[-1] == ("good",)  # the survivors are locked together at the end


def test_requirements_that_only_conflict_together_are_each_unresolvable() -> None:
    uv = ScriptedUv(_resolves(conflict={"left", "right"}))
    resolver = scripted(uv, index=_Index({}))
    result = resolver(["left", "right"], arch="amd64", exclude_newer=CUTOFF)
    assert sorted((u.requirement, u.reason) for u in result.unprovided) == [
        ("left", "unresolvable"),
        ("right", "unresolvable"),
    ]
    assert result.lock_text == ""
    assert result.provided == ()


def test_survivors_whose_closure_is_over_the_cap_are_cap_exceeded_beside_the_refused() -> None:
    uv = ScriptedUv(_resolves(refuse={"solo"}))
    resolver = scripted(uv, index=_Index({}), max_packages=1)
    result = resolver(["a", "b", "solo"], arch="amd64", exclude_newer=CUTOFF)
    assert sorted((u.requirement, u.reason) for u in result.unprovided) == [
        ("a", "cap-exceeded"),
        ("b", "cap-exceeded"),
        ("solo", "unresolvable"),
    ]
    assert result.lock_text == ""


@pytest.mark.parametrize("stage", ["single", "survivors"])
def test_an_outage_found_while_diagnosing_is_infrastructure_not_a_verdict(stage: str) -> None:
    no_solution = _done(1, stderr="No solution found")
    outage = _done(2, stderr="dns error: failed to lookup address")
    ok = _done(stdout=fake_lock({"a": "1.0"}))
    if stage == "single":
        script = _answers(no_solution, ok, outage)  # whole, canary, "a" alone
    else:
        script = _answers(no_solution, ok, ok, no_solution, ok, outage)  # ..., "solo", canary
    uv = ScriptedUv(script)
    with pytest.raises(DependencyInfraError, match="unavailable"):
        scripted(uv, index=_Index({}))(["a", "solo"], arch="amd64", exclude_newer=CUTOFF)


def test_a_diagnosis_that_runs_past_the_time_cap_is_infrastructure_never_a_verdict(
    clock: list[float],
) -> None:
    seen: list[tuple[str, ...]] = []

    def respond(lines: tuple[str, ...]) -> Outcome:
        seen.append(lines)
        if len(seen) == 3:  # "solo" alone: the clock lands past the work deadline (90 s)
            clock[0] = 100.0  # ... but inside the total cap (120 s), so the canary still runs
        return _done() if lines == ("fast==1.0",) else _done(1, stderr="No solution found")

    uv = ScriptedUv(respond, clock)
    resolver = scripted(uv, index=_Index({}), timeout_retries=0)
    with pytest.raises(DependencyInfraError, match=r"timed out in 1 attempt"):
        resolver(["solo"], arch="amd64", exclude_newer=CUTOFF)


@pytest.mark.parametrize(
    "stdout",
    [
        "this is [not toml",
        (
            '[[packages]]\nversion = "1"\n'
            'wheels = [{ url = "https://files.example/x.whl", size = 1 }]\n'
        ),
    ],
    ids=["not-toml", "package-without-a-name"],
)
def test_a_lock_uv_printed_but_cannot_be_read_is_an_infrastructure_failure(stdout: str) -> None:
    uv = ScriptedUv(_answers(_done(stdout=stdout)))
    with pytest.raises(DependencyInfraError, match="unreadable lock"):
        scripted(uv)(["pkg"], arch="amd64", exclude_newer=CUTOFF)


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"max_packages": 1}, "cap-exceeded"),
        ({"max_bytes": 19}, "cap-exceeded"),
        ({"wheel_url_prefixes": ("https://other.example/",)}, "direct-reference"),
    ],
)
def test_a_closure_over_a_cap_or_from_outside_the_index_provides_nothing(
    options: dict[str, object], reason: str
) -> None:
    uv = ScriptedUv(_resolves())
    result = scripted(uv, **options)(["pkg", "dep"], arch="amd64", exclude_newer=CUTOFF)
    assert {(u.requirement, u.reason) for u in result.unprovided} == {
        ("pkg", reason),
        ("dep", reason),
    }
    assert result.lock_text == ""
    assert result.provided == ()
    within = scripted(ScriptedUv(_resolves()), max_packages=2, max_bytes=20)
    assert not within(["pkg", "dep"], arch="amd64", exclude_newer=CUTOFF).unprovided


def test_a_find_links_directory_is_its_own_index(tmp_path: Path) -> None:
    make_sdist(tmp_path, "solo")
    uv = ScriptedUv(_resolves(refuse={"solo"}))
    resolver = UvResolver(
        run=uv, compile_args=("--no-index", "--find-links", str(tmp_path)), canary="fast==1.0"
    )
    result = resolver(["solo"], arch="amd64", exclude_newer=CUTOFF)
    assert [(u.requirement, u.reason) for u in result.unprovided] == [("solo", "no-wheel")]
    assert uv.argvs[0][-3:] == ["--no-index", "--find-links", str(tmp_path)]


def test_the_default_index_is_the_simple_api(monkeypatch: pytest.MonkeyPatch) -> None:
    document = {"files": [{"filename": "solo-1.0.tar.gz", "upload-time": "2026-01-01T00:00:00Z"}]}
    seen: list[str] = []

    def answer(request: object, timeout: float) -> io.BytesIO:
        seen.append(request.full_url)  # type: ignore[attr-defined]
        return io.BytesIO(json.dumps(document).encode())

    monkeypatch.setattr(resolver_module.urllib.request, "urlopen", answer)
    uv = ScriptedUv(_resolves(refuse={"solo"}))
    result = scripted(uv, index_url="https://index.example/simple")(
        ["solo"], arch="amd64", exclude_newer=CUTOFF
    )
    assert [(u.requirement, u.reason) for u in result.unprovided] == [("solo", "no-wheel")]
    assert seen == ["https://index.example/simple/solo/"]


def test_index_metadata_cannot_outlive_its_remaining_resolution_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [10.0]

    def stalled(request: object, timeout: float) -> None:
        clock[0] += timeout
        raise TimeoutError

    monkeypatch.setattr(resolver_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(resolver_module.urllib.request, "urlopen", stalled)
    with pytest.raises(DependencyInfraError):
        SimpleIndex("https://index.example", deadline=12.0).files("pkg")
    assert clock[0] <= 12.0


# --------------------------------------------------------------- classification
CUT = datetime(2026, 10, 1, tzinfo=UTC)
EARLY, LATE = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("requirement", "files", "expected"),
    [
        ("p", None, "unresolvable"),
        ("p>=9", [IndexFile("p-1.0.tar.gz", EARLY)], "unresolvable"),
        ("p", [IndexFile("p-1.0.tar.gz", EARLY)], "no-wheel"),
        ("p", [IndexFile("p-1.0.tar.gz", LATE)], "after-cutoff"),
        ("p", [IndexFile("p-1.0-py3-none-any.whl")], "unresolvable"),
        ("p", [IndexFile("p-1.0-cp312-cp312-manylinux_2_17_x86_64.whl", EARLY)], "unresolvable"),
        ("p", [IndexFile("p-1.0-cp312-cp312-manylinux2014_x86_64.whl", EARLY)], "unresolvable"),
        ("p", [IndexFile("p-1.0-cp312-cp312-manylinux1_x86_64.whl", EARLY)], "unresolvable"),
        ("p", [IndexFile("p-1.0-cp312-abi3-manylinux_2_28_x86_64.whl", EARLY)], "unresolvable"),
        ("p", [IndexFile("p-1.0-cp312-cp312-linux_x86_64.whl", EARLY)], "unresolvable"),
        ("p", [IndexFile("p-1.0-cp312-cp312-macosx_11_0_arm64.whl", EARLY)], "no-wheel"),
        ("p", [IndexFile("p-1.0-cp311-cp311-manylinux_2_28_x86_64.whl", EARLY)], "no-wheel"),
        ("p", [IndexFile("p-1.0-cp313-abi3-manylinux_2_28_x86_64.whl", EARLY)], "no-wheel"),
        ("p", [IndexFile("p-1.0-cp3x-abi3-manylinux_2_28_x86_64.whl", EARLY)], "no-wheel"),
        ("p", [IndexFile("p-1.0-cp312-cp312-manylinux_2_34_x86_64.whl", EARLY)], "no-wheel"),
        ("p", [IndexFile("p-1.0-cp312-cp312-manylinux_2_28_aarch64.whl", EARLY)], "no-wheel"),
        ("p", [IndexFile("p-1.0-cp312-cp312-manylinux2014_aarch64.whl", EARLY)], "no-wheel"),
        ("p", [IndexFile("garbage.whl", EARLY)], "unresolvable"),
        ("p", [IndexFile("README", EARLY)], "unresolvable"),
    ],
)
def test_failures_are_classified_from_the_index_listing(
    requirement: str, files: list[IndexFile] | None, expected: str
) -> None:
    assert classify_failure(requirement, files, arch="amd64", cutoff=CUT) == expected


def test_a_wheel_for_the_other_architecture_does_not_fit() -> None:
    arm = [IndexFile("p-1.0-cp312-cp312-manylinux_2_28_aarch64.whl", EARLY)]
    assert classify_failure("p", arm, arch="amd64", cutoff=CUT) == "no-wheel"
    assert classify_failure("p", arm, arch="arm64", cutoff=CUT) == "unresolvable"


def test_the_simple_index_reads_files_and_upload_times(monkeypatch: pytest.MonkeyPatch) -> None:
    document = {
        "files": [
            {"filename": "p-1.0.tar.gz", "upload-time": "2026-01-01T00:00:00.000000Z"},
            {"filename": "p-1.0-py3-none-any.whl"},
        ]
    }
    seen: list[str] = []

    def answer(request: object, timeout: float) -> io.BytesIO:
        seen.append(request.full_url)  # type: ignore[attr-defined]
        return io.BytesIO(json.dumps(document).encode())

    monkeypatch.setattr(resolver_module.urllib.request, "urlopen", answer)
    files = SimpleIndex("https://index.example/simple/").files("P_Q")
    assert files is not None
    assert [f.filename for f in files] == ["p-1.0.tar.gz", "p-1.0-py3-none-any.whl"]
    assert files[0].uploaded == datetime(2026, 1, 1, tzinfo=UTC)
    assert files[1].uploaded is None
    assert seen == ["https://index.example/simple/p-q/"]


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (urllib.error.HTTPError("u", 404, "nf", {}, None), None),  # type: ignore[arg-type]
        (urllib.error.HTTPError("u", 503, "down", {}, None), DependencyInfraError),  # type: ignore[arg-type]
        (urllib.error.URLError("no route"), DependencyInfraError),
    ],
)
def test_the_simple_index_maps_http_failures(
    monkeypatch: pytest.MonkeyPatch, failure: Exception, expected: object
) -> None:
    def refuse(request: object, timeout: float) -> None:
        raise failure

    monkeypatch.setattr(resolver_module.urllib.request, "urlopen", refuse)
    if expected is None:
        assert SimpleIndex().files("p") is None
    else:
        with pytest.raises(DependencyInfraError):
            SimpleIndex().files("p")


def test_the_directory_index_lists_matching_files(tmp_path: Path) -> None:
    make_wheel(tmp_path, "pkg")
    make_sdist(tmp_path, "pkg", "2.0")
    (tmp_path / "notes.txt").write_text("")
    listing = DirectoryIndex(tmp_path).files("PKG")
    assert listing is not None
    assert len(listing) == 2
    assert DirectoryIndex(tmp_path).files("other") is None


# ------------------------------------------------------------------------ pins
def test_resolve_pins_resolves_every_set_for_both_architectures() -> None:
    fake = FakeResolver()
    pins, locks = resolve_pins(
        {"checkpoint_1": ["flask>=3", "pyyaml"], "checkpoint_2": ["pyyaml", "flask>=3"], "x": []},
        ["pandas"],
        exclude_newer=CUTOFF,
        resolver=fake,
    )
    step_set = set_sha256(["flask>=3", "pyyaml"])
    assert pins.steps == {"checkpoint_1": step_set, "checkpoint_2": step_set}
    assert pins.seed == set_sha256(["pandas"])
    assert pins.grader is not None
    assert pins.grader != pins.seed
    assert set(pins.sets) == {step_set, pins.seed, pins.grader}
    assert pins.resolver == "uv@0.0-test"
    assert pins.exclude_newer == CUTOFF
    assert pins.policy == "declared-wheels/1"
    assert len(pins.platforms) == 2
    assert {arch for _, arch in locks} == {"amd64", "arm64"}
    for key, arch in pins.lock_keys():
        assert pins.sets[key].lock(arch) == lock_sha256(locks[(key, arch)])
    assert {call[2] for call in fake.calls} == {CUTOFF}
    tools = pins.sets[pins.grader].requirements
    assert "pytest==9.0.3" in tools
    assert "pandas" in tools


def test_a_problem_without_test_dependencies_has_no_seed() -> None:
    pins, _ = resolve_pins({"s": []}, [], exclude_newer=CUTOFF, resolver=FakeResolver())
    assert pins.seed is None
    assert pins.steps == {}
    assert pins.grader is not None


def test_diverging_architectures_exclude_the_problem() -> None:
    fake = FakeResolver(arch_versions={"arm64": "2.0"})
    with pytest.raises(DependencyUnresolvable, match="arch-mismatch") as caught:
        resolve_pins({"s": ["pkg"]}, [], exclude_newer=CUTOFF, resolver=fake)
    assert {item.reason for item in caught.value.unprovided} == {"arch-mismatch"}


def test_any_unprovided_requirement_excludes_the_problem() -> None:
    fake = FakeResolver(unprovided={"solo": "no-wheel"})
    with pytest.raises(DependencyUnresolvable, match="solo: no-wheel") as caught:
        resolve_pins({"s": ["solo"]}, [], exclude_newer=CUTOFF, resolver=fake)
    assert [(u.requirement, u.reason) for u in caught.value.unprovided] == [("solo", "no-wheel")]


def test_a_resolver_outage_during_import_is_an_infrastructure_error_not_an_exclusion() -> None:
    fake = FakeResolver(error=DependencyInfraError("pypi down"))
    with pytest.raises(DependencyInfraError):
        resolve_pins({"s": ["pkg"]}, [], exclude_newer=CUTOFF, resolver=fake)


# ------------------------------------------------------------------ wheel cache
def _lock_for(tmp_path: Path) -> tuple[str, Path]:
    wheel = make_wheel(tmp_path / "src", "pkg")
    return fake_lock({"pkg": "1.0"}, wheels={"pkg": wheel}), wheel


def test_wheels_are_fetched_once_hash_checked_and_then_served_from_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock, wheel = _lock_for(tmp_path)
    cache = tmp_path / "cache"
    assert wheel_cache.fetch_wheels(lock, tmp_path / "one", cache_root=cache) == 1
    assert (tmp_path / "one" / wheel.name).read_bytes() == wheel.read_bytes()

    def blocked(url: str) -> None:
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(wheel_cache, "open_wheel", blocked)
    assert wheel_cache.fetch_wheels(lock, tmp_path / "two", cache_root=cache) == 1
    assert (tmp_path / "two" / wheel.name).is_file()


def test_a_cache_miss_with_the_index_unreachable_is_an_infrastructure_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock, _ = _lock_for(tmp_path)

    def blocked(url: str) -> None:
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(wheel_cache, "open_wheel", blocked)
    with pytest.raises(DependencyInfraError, match="could not fetch"):
        wheel_cache.fetch_wheels(lock, tmp_path / "out", cache_root=tmp_path / "empty-cache")
    assert not list((tmp_path / "empty-cache").rglob("*.part"))


def test_a_wheel_that_differs_from_its_locked_hash_is_refused(tmp_path: Path) -> None:
    lock, wheel = _lock_for(tmp_path)
    wheel.write_bytes(wheel.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="does not match its locked sha256"):
        wheel_cache.fetch_wheels(lock, tmp_path / "out", cache_root=tmp_path / "cache")
    assert not list((tmp_path / "cache").rglob("*.part"))


def test_an_oversized_wheel_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock, _ = _lock_for(tmp_path)
    monkeypatch.setattr(wheel_cache, "MAX_WHEEL_BYTES", 5)
    with pytest.raises(ValueError, match="byte cap"):
        wheel_cache.fetch_wheels(lock, tmp_path / "out", cache_root=tmp_path / "cache")


def test_only_https_and_file_urls_are_opened_and_a_lock_needs_hashes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not one of"):
        wheel_cache.open_wheel("http://insecure.example/p.whl")
    bare = (
        'lock-version = "1.0"\n[[packages]]\nname = "p"\nversion = "1"\n'
        'wheels = [{ url = "file:///p.whl" }]\n'
    )
    with pytest.raises(ValueError, match="sha256"):
        wheel_cache.fetch_wheels(bare, tmp_path / "out", cache_root=tmp_path / "c")


def test_the_default_cache_lives_under_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RAIDAR_VAR", str(tmp_path))
    assert wheel_cache.default_cache_root() == tmp_path.resolve() / "cache" / "wheels"


# ------------------------------------------------------------- pin and lock records
def test_a_pin_names_its_sets_by_step_and_its_locks_by_set_and_architecture() -> None:
    pins, locks = resolve_pins(
        {"checkpoint_1": ["flask"], "checkpoint_2": []},
        [],
        exclude_newer=CUTOFF,
        resolver=FakeResolver(),
    )
    step_set = pins.set_for_step("checkpoint_1")
    assert step_set is not None
    assert step_set.requirements == ("flask",)
    assert pins.set_for_step("checkpoint_2") is None
    assert pins.set_for_step("unknown") is None
    keys = pins.lock_keys()
    assert keys == sorted(keys)
    assert {arch for _, arch in keys} == {"amd64", "arm64"}
    assert len(keys) == 2 * len(pins.sets)
    assert {pins.lock_filename(key, arch) for key, arch in pins.lock_keys()} == {
        DependencyLocks(Path()).path(key, arch).name for key, arch in pins.lock_keys()
    }
    assert pins.digest() == pins.digest()
    moved = pins.model_copy(update={"exclude_newer": "2020-01-01T00:00:00Z"})
    assert moved.digest() != pins.digest()
    assert set(locks) == set(pins.lock_keys())


def test_recorded_locks_are_read_back_only_when_they_match_their_pin(tmp_path: Path) -> None:
    pins, locks = resolve_pins(
        {"checkpoint_1": ["flask"]}, [], exclude_newer=CUTOFF, resolver=FakeResolver()
    )
    directory = DependencyLocks(tmp_path / "deps")
    (key, arch), text = sorted(locks.items())[0]
    assert not directory.has(key, arch)
    with pytest.raises(DependencyLockError, match="missing dependency lock"):
        directory.read_pins(pins)
    directory.write_all(locks)
    assert directory.has(key, arch)
    assert directory.read_pins(pins) == locks
    directory.write(key, arch, text + "# tampered\n")
    with pytest.raises(DependencyLockError, match="differs from its pin"):
        directory.read_pins(pins)


# ------------------------------------------------------------ import-time resolution
IMPORT_TIME = "2026-10-27T12:00:00Z"
PROBLEM = ProblemId.parse("scb/adder@abcdef012345")
STEPS = ["checkpoint_1", "checkpoint_2"]


def _manifest(
    *, dependencies: DependencyPins | None = None, refusal: DependencyRefusal | None = None
) -> ProblemManifest:
    return ProblemManifest(
        ref=str(PROBLEM),
        mode=ProblemMode.MULTI_CHECKPOINT,
        checkpoints=1,
        steps=(
            ProblemStep(
                name="checkpoint_1",
                checkpoint="checkpoint_1",
                instruction_sha256="i",
                test_files=("test_checkpoint_1.py",),
            ),
        ),
        source=ProblemSource(
            dataset="scb",
            url="fixture://adder",
            commit="abcdef012345" + "0" * 28,
            problem="adder",
            problem_tree="t",
            converter_tree="c",
            converted_task_sha256="x",
        ),
        pack=PackBinding(id="python", version=1, image="img"),
        task_sha256="t",
        bundle_sha256="b",
        references_sha256="r",
        dependencies=dependencies,
        dependency_refusal=refusal,
    )


def _references(root: Path, declared: dict[str, list[str]]) -> Path:
    for step, requirements in declared.items():
        directory = root / "references" / step
        directory.mkdir(parents=True)
        listed = ", ".join(f'"{item}"' for item in requirements)
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "app"\ndependencies = [{listed}]\n'
        )
    return root / "references"


@pytest.fixture(name="import_clock")
def import_clock_fixture(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    now = [IMPORT_TIME]
    monkeypatch.setattr(problem_dependencies, "cutoff_now", lambda: now[0])
    return now


def test_the_import_time_cutoff_is_rfc_3339_utc_to_the_second() -> None:
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", problem_dependencies.cutoff_now())


def test_a_new_problem_resolves_every_declared_set_under_the_import_time_cutoff(
    tmp_path: Path, import_clock: list[str]
) -> None:
    references = _references(tmp_path, {"checkpoint_1": ["flask"], "checkpoint_2": []})
    fake = FakeResolver()
    result = resolve_dependencies(
        ProblemStore(tmp_path / "store"), PROBLEM, None, references, STEPS, ("pyyaml",), fake
    )
    assert result.refusal is None
    assert result.pins is not None
    assert result.locks is not None
    assert result.pins.exclude_newer == IMPORT_TIME
    assert set(result.pins.steps) == {"checkpoint_1"}  # an empty set needs no lock
    assert result.pins.seed == set_sha256(["pyyaml"])
    assert set(result.locks) == set(result.pins.lock_keys())
    assert {call[2] for call in fake.calls} == {IMPORT_TIME}


def test_a_recorded_pin_for_the_same_declarations_is_reused_with_its_committed_locks(
    tmp_path: Path, import_clock: list[str]
) -> None:
    store = ProblemStore(tmp_path / "store")
    references = _references(tmp_path, {"checkpoint_1": ["flask"], "checkpoint_2": []})
    first = resolve_dependencies(
        store, PROBLEM, None, references, STEPS, ("pyyaml",), FakeResolver()
    )
    write_locks(store, PROBLEM, first)
    import_clock[0] = "2027-01-01T00:00:00Z"
    again = FakeResolver()
    reused = resolve_dependencies(
        store, PROBLEM, _manifest(dependencies=first.pins), references, STEPS, ("pyyaml",), again
    )
    assert again.calls == []
    assert reused.pins == first.pins
    assert reused.locks == first.locks


@pytest.mark.parametrize("changed", ["step", "seed"])
def test_a_changed_declaration_resolves_again_under_the_retained_cutoff(
    tmp_path: Path, import_clock: list[str], changed: str
) -> None:
    store = ProblemStore(tmp_path / "store")
    references = _references(tmp_path, {"checkpoint_1": ["flask"], "checkpoint_2": []})
    first = resolve_dependencies(
        store, PROBLEM, None, references, STEPS, ("pyyaml",), FakeResolver()
    )
    write_locks(store, PROBLEM, first)
    import_clock[0] = "2027-01-01T00:00:00Z"
    again = FakeResolver()
    tests = ("pyyaml", "rich") if changed == "seed" else ("pyyaml",)
    if changed == "step":
        (references / "checkpoint_1" / "pyproject.toml").write_text(
            '[project]\nname = "app"\ndependencies = ["flask>=3"]\n'
        )
    result = resolve_dependencies(
        store, PROBLEM, _manifest(dependencies=first.pins), references, STEPS, tests, again
    )
    assert again.calls
    assert {call[2] for call in again.calls} == {IMPORT_TIME}  # the recorded cutoff, not now
    assert result.pins is not None
    assert result.pins != first.pins


def test_an_unprovided_requirement_is_a_refusal_that_keeps_its_cutoff_on_reimport(
    tmp_path: Path, import_clock: list[str]
) -> None:
    store = ProblemStore(tmp_path / "store")
    references = _references(tmp_path, {"checkpoint_1": ["solo"], "checkpoint_2": []})
    refused = resolve_dependencies(
        store, PROBLEM, None, references, STEPS, (), FakeResolver(unprovided={"solo": "no-wheel"})
    )
    assert refused.pins is None
    assert refused.locks is None
    refusal = refused.refusal
    assert refusal is not None
    assert refusal.exclude_newer == IMPORT_TIME
    assert [(u.requirement, u.reason) for u in refusal.unprovided] == [("solo", "no-wheel")]
    assert "solo: no-wheel" in refusal.detail
    import_clock[0] = "2027-01-01T00:00:00Z"
    retry = FakeResolver()
    recorded = _manifest(refusal=refusal)
    resolve_dependencies(store, PROBLEM, recorded, references, STEPS, (), retry)
    assert {call[2] for call in retry.calls} == {IMPORT_TIME}


def test_a_resolver_outage_is_infrastructure_never_a_refusal(
    tmp_path: Path, import_clock: list[str]
) -> None:
    references = _references(tmp_path, {"checkpoint_1": ["flask"]})
    fake = FakeResolver(error=DependencyInfraError("index unreachable"))
    with pytest.raises(DependencyInfraError):
        resolve_dependencies(
            ProblemStore(tmp_path / "store"), PROBLEM, None, references, ["checkpoint_1"], (), fake
        )


def test_committed_locks_are_replaced_and_a_refused_problem_keeps_none(
    tmp_path: Path, import_clock: list[str]
) -> None:
    store = ProblemStore(tmp_path / "store")
    references = _references(tmp_path, {"checkpoint_1": ["flask"]})
    resolved = resolve_dependencies(
        store, PROBLEM, None, references, ["checkpoint_1"], (), FakeResolver()
    )
    write_locks(store, PROBLEM, resolved)
    stale = store.deps_dir(PROBLEM) / "stale.toml"
    stale.write_text("# not a lock of this pin\n")
    write_locks(store, PROBLEM, resolved)
    assert not stale.exists()
    assert resolved.pins is not None
    assert DependencyLocks(store.deps_dir(PROBLEM)).read_pins(resolved.pins) == resolved.locks
    refusal = DependencyRefusal(detail="solo: no-wheel", exclude_newer=IMPORT_TIME)
    write_locks(store, PROBLEM, ProblemDependencies(refusal=refusal))
    assert not store.deps_dir(PROBLEM).exists()
