"""``declared-wheels/1`` resolution: deterministic locks, classified refusals, caps, outages.

The real ``uv`` resolves against a local ``--no-index --find-links`` directory of
hand-made wheels and sdists, so no test reaches the network and no package code
can run (a planted ``setup.py`` would fail the test loudly if it did).
"""

from __future__ import annotations

import io
import json
import stat
import subprocess
import sys
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import pytest

from raidar.contract import wheel_cache
from raidar.contract.wheels import DependencyInfraError
from raidar.grading import resolver as resolver_module
from raidar.grading.dependency_pins import lock_sha256, set_sha256
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

CANARY = "raidar-canary==1.0"


@pytest.fixture(autouse=True)
def local_wheel_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture locks name ``file://`` wheels; production accepts only the index's own host."""
    monkeypatch.setattr(wheel_cache, "WHEEL_URL_PREFIXES", ("file://",))


@pytest.fixture
def links(tmp_path: Path) -> Path:
    directory = tmp_path / "links"
    make_wheel(directory, "raidar-canary")
    return directory


def local(links: Path, **options: object) -> UvResolver:
    return UvResolver(
        compile_args=("--no-index", "--find-links", str(links)), canary=CANARY, **options
    )  # type: ignore[arg-type]


def test_the_same_input_gives_a_byte_identical_lock(links: Path) -> None:
    make_wheel(links, "dep-a", "2.0")
    make_wheel(links, "pkg", "1.0", requires=["dep-a>=2"])
    first = local(links)(["pkg"], arch="amd64", exclude_newer=CUTOFF)
    second = local(links)(["pkg"], arch="amd64", exclude_newer=CUTOFF)
    assert first.lock_text == second.lock_text
    assert lock_sha256(first.lock_text) == lock_sha256(second.lock_text)
    assert first.provided == ("pkg==1.0",)
    assert [name for name, _ in pylock_packages(first.lock_text)] == ["dep-a", "pkg"]
    assert first.packages == 2
    assert not first.unprovided


def test_an_sdist_only_package_is_no_wheel_and_its_setup_is_never_run(
    links: Path, tmp_path: Path
) -> None:
    marker = tmp_path / "setup-ran"
    make_sdist(links, "solo", setup_py=f"open({str(marker)!r}, 'w').write('x')\n")
    result = local(links)(["solo"], arch="amd64", exclude_newer=CUTOFF)
    assert [(u.requirement, u.reason) for u in result.unprovided] == [("solo", "no-wheel")]
    assert result.lock_text == ""
    assert result.provided == ()
    assert not marker.exists()


def test_a_survivor_is_still_provided_beside_a_refused_requirement(links: Path) -> None:
    make_wheel(links, "pkg")
    make_sdist(links, "solo")
    result = local(links)(["pkg", "solo"], arch="arm64", exclude_newer=CUTOFF)
    assert result.provided == ("pkg==1.0",)
    assert [(u.requirement, u.reason) for u in result.unprovided] == [("solo", "no-wheel")]
    assert pylock_packages(result.lock_text) == [("pkg", "1.0")]


def test_a_release_after_the_cutoff_is_after_cutoff(links: Path) -> None:
    late = datetime(2026, 11, 1, tzinfo=UTC)
    index = _Index({"late": [IndexFile("late-2.0-py3-none-any.whl", late)]})
    result = local(links, index=index)(["late>=2"], arch="amd64", exclude_newer=CUTOFF)
    assert [(u.requirement, u.reason) for u in result.unprovided] == [("late>=2", "after-cutoff")]


class _Index:
    def __init__(self, projects: dict[str, list[IndexFile]]) -> None:
        self.projects = projects

    def files(self, name: str) -> list[IndexFile] | None:
        return self.projects.get(name)


def test_a_missing_project_is_unresolvable_and_a_conflict_marks_every_survivor(
    links: Path,
) -> None:
    make_wheel(links, "left", requires=["shared==1"])
    make_wheel(links, "right", requires=["shared==2"])
    make_wheel(links, "shared", "1")
    make_wheel(links, "shared", "2")
    resolver = local(links)
    missing = resolver(["ghost"], arch="amd64", exclude_newer=CUTOFF)
    assert [u.reason for u in missing.unprovided] == ["unresolvable"]
    conflict = resolver(["left", "right"], arch="amd64", exclude_newer=CUTOFF)
    assert sorted((u.requirement, u.reason) for u in conflict.unprovided) == [
        ("left", "unresolvable"),
        ("right", "unresolvable"),
    ]


def test_direct_references_and_unparseable_lines_are_refused_without_running_uv() -> None:
    resolver = UvResolver(uv_bin="/nonexistent/uv")
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


def test_a_lock_with_a_source_outside_the_index_is_a_direct_reference(links: Path) -> None:
    make_wheel(links, "pkg")
    strict = local(links, wheel_url_prefixes=("https://files.pythonhosted.org/",))
    result = strict(["pkg"], arch="amd64", exclude_newer=CUTOFF)
    assert [(u.requirement, u.reason) for u in result.unprovided] == [("pkg", "direct-reference")]
    assert result.lock_text == ""


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


def test_exceeding_a_cap_is_cap_exceeded(links: Path) -> None:
    make_wheel(links, "dep-a")
    make_wheel(links, "pkg", requires=["dep-a"])
    by_count = local(links, max_packages=1)(["pkg"], arch="amd64", exclude_newer=CUTOFF)
    assert [(u.requirement, u.reason) for u in by_count.unprovided] == [("pkg", "cap-exceeded")]
    assert by_count.lock_text == ""
    within = local(links, max_packages=2)(["pkg"], arch="amd64", exclude_newer=CUTOFF)
    assert not within.unprovided


def test_the_byte_cap_counts_every_wheel_a_fetch_would_download() -> None:
    lock = fake_lock({"pkg": "1", "dep": "2"})  # two wheels of 10 bytes each
    host = ("https://files.example/",)
    over = UvResolver(max_bytes=19, wheel_url_prefixes=host)._within_caps(lock, ["pkg"])
    assert over[0] == ""
    assert [u.reason for u in over[1]] == ["cap-exceeded"]
    within = UvResolver(max_bytes=20, wheel_url_prefixes=host)._within_caps(lock, ["pkg"])
    assert within == (lock, [])


def _stub_uv(tmp_path: Path, body: str) -> str:
    script = tmp_path / "uv"
    script.write_text(f"#!{sys.executable}\nimport sys, time\n{body}")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


_LOCK = fake_lock({"slow": "1.0"})


def test_a_resolution_over_its_time_cap_is_infrastructure_never_cap_exceeded(
    tmp_path: Path,
) -> None:
    uv = _stub_uv(
        tmp_path,
        "text = open(sys.argv[3]).read()\n"
        "if 'slow' in text:\n    time.sleep(30)\n"
        f"print({_LOCK!r})\n",
    )
    resolver = UvResolver(uv_bin=uv, timeout_s=1.0, canary="fast==1.0")
    with pytest.raises(DependencyInfraError, match=r"timed out in 1 attempt.*not configured"):
        resolver(["slow"], arch="amd64", exclude_newer=CUTOFF)


def _timing_out_run(monkeypatch: pytest.MonkeyPatch, calls: list[float]) -> UvResolver:
    clock = [0.0]

    def run(
        argv: list[str], *, timeout: float, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(timeout)
        clock[0] += timeout
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(resolver_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(resolver_module.subprocess, "run", run)
    return UvResolver(canary="fast==1.0", timeout_retries=2)


def test_timeouts_are_retried_up_to_the_bound_then_fail_as_infrastructure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[float] = []
    resolver = _timing_out_run(monkeypatch, calls)
    with pytest.raises(DependencyInfraError, match=r"timed out in 3 attempt.*retry bound: 2"):
        resolver(["slow"], arch="amd64", exclude_newer=CUTOFF)
    assert len(calls) == 3
    assert all(call <= resolver.timeout_s for call in calls)


def test_a_retry_that_succeeds_records_its_attempts(tmp_path: Path) -> None:
    state = tmp_path / "state"
    uv = _stub_uv(
        tmp_path,
        f"import os\nflag = {str(state)!r}\n"
        "if not os.path.exists(flag):\n    open(flag, 'w').close()\n    time.sleep(30)\n"
        f"print({_LOCK!r})\n",
    )
    resolver = UvResolver(
        uv_bin=uv,
        timeout_s=1.0,
        canary="fast==1.0",
        timeout_retries=1,
        wheel_url_prefixes=("https://files.example/",),
    )
    result = resolver(["slow"], arch="amd64", exclude_newer=CUTOFF)
    assert result.attempts == 2
    assert not result.unprovided


def test_an_unreachable_index_is_an_infrastructure_failure_never_unprovided(
    tmp_path: Path,
) -> None:
    uv = _stub_uv(
        tmp_path,
        "sys.stderr.write('error sending request for url (https://pypi.org/simple/x/)')\n"
        "sys.exit(2)\n",
    )
    with pytest.raises(DependencyInfraError, match="unavailable"):
        UvResolver(uv_bin=uv)(["pkg"], arch="amd64", exclude_newer=CUTOFF)


def test_a_failing_canary_is_an_infrastructure_failure(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(DependencyInfraError):
        local(empty)(["pkg"], arch="amd64", exclude_newer=CUTOFF)


def test_a_missing_uv_is_an_infrastructure_failure(tmp_path: Path) -> None:
    resolver = UvResolver(uv_bin=str(tmp_path / "no-uv"))
    with pytest.raises(DependencyInfraError, match="uv is unavailable"):
        resolver(["pkg"], arch="amd64", exclude_newer=CUTOFF)
    with pytest.raises(DependencyInfraError, match="uv is unavailable"):
        _ = resolver.identity


def test_the_resolver_identity_is_the_uv_version(tmp_path: Path) -> None:
    uv = _stub_uv(tmp_path, "print('uv 9.8.7 (abc 2026-01-01)')\n")
    assert UvResolver(uv_bin=uv).identity == "uv@9.8.7"


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
