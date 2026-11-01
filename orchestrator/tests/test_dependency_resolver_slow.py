"""Slow-tier behaviours split from :mod:`tests.test_dependency_resolver`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from raidar.contract.wheels import DependencyInfraError
from raidar.grading.dependency_pins import lock_sha256
from raidar.grading.resolver import (
    IndexFile,
    UvResolver,
    pylock_packages,
)
from tests.dependency_support import CUTOFF, make_sdist, make_wheel
from tests.test_dependency_resolver import (
    _LOCK,
    _Index,
    _stub_uv,
    local,
)
from tests.test_dependency_resolver import (
    links_fixture as links_fixture,
)

pytestmark = pytest.mark.slow


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


def test_a_lock_with_a_source_outside_the_index_is_a_direct_reference(links: Path) -> None:
    make_wheel(links, "pkg")
    strict = local(links, wheel_url_prefixes=("https://files.pythonhosted.org/",))
    result = strict(["pkg"], arch="amd64", exclude_newer=CUTOFF)
    assert [(u.requirement, u.reason) for u in result.unprovided] == [("pkg", "direct-reference")]
    assert result.lock_text == ""


def test_exceeding_a_cap_is_cap_exceeded(links: Path) -> None:
    make_wheel(links, "dep-a")
    make_wheel(links, "pkg", requires=["dep-a"])
    by_count = local(links, max_packages=1)(["pkg"], arch="amd64", exclude_newer=CUTOFF)
    assert [(u.requirement, u.reason) for u in by_count.unprovided] == [("pkg", "cap-exceeded")]
    assert by_count.lock_text == ""
    within = local(links, max_packages=2)(["pkg"], arch="amd64", exclude_newer=CUTOFF)
    assert not within.unprovided


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
    with pytest.raises(DependencyInfraError, match=r"timed out in 2 attempt.*retry bound: 1"):
        resolver(["slow"], arch="amd64", exclude_newer=CUTOFF)


def test_default_resolver_recovers_from_one_timeout_with_an_identical_retry(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    log = tmp_path / "log"
    uv = _stub_uv(
        tmp_path,
        f"import os\nflag = {str(state)!r}\n"
        f"open({str(log)!r}, 'a').write("
        "repr(sys.argv[1:3] + sys.argv[4:]) + repr(open(sys.argv[3]).read()) + '\\n')\n"
        "if not os.path.exists(flag):\n    open(flag, 'w').close()\n    time.sleep(30)\n"
        f"print({_LOCK!r})\n",
    )
    resolver = UvResolver(
        uv_bin=uv,
        timeout_s=1.0,
        canary="fast==1.0",
        wheel_url_prefixes=("https://files.example/",),
    )
    result = resolver(["slow"], arch="amd64", exclude_newer=CUTOFF)
    assert result.attempts == 2
    assert not result.unprovided
    requests = log.read_text().splitlines()
    assert len(requests) == 2
    assert requests[0] == requests[1]
    assert "--exclude-newer" in requests[0]


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
