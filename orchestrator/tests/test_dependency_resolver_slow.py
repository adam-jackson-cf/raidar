"""The real ``uv`` seam of ``declared-wheels/1`` resolution.

The real ``uv`` resolves against a local ``--no-index --find-links`` directory of hand-made
wheels and sdists, so no test reaches the network and no package code can run (a planted
``setup.py`` would fail the test loudly if it did). Decisions about outcomes, caps, outages and
timeouts are proved with a scripted shell in :mod:`tests.test_dependency_resolver`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.contract.wheels import DependencyInfraError
from raidar.grading.dependency_pins import lock_sha256
from raidar.grading.resolver import UvResolver, pylock_packages
from tests.dependency_support import CUTOFF, make_sdist, make_wheel

pytestmark = pytest.mark.slow

CANARY = "raidar-canary==1.0"


@pytest.fixture(name="links")
def links_fixture(tmp_path: Path) -> Path:
    directory = tmp_path / "links"
    make_wheel(directory, "raidar-canary")
    return directory


def local(links: Path) -> UvResolver:
    return UvResolver(compile_args=("--no-index", "--find-links", str(links)), canary=CANARY)


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


def test_a_failing_canary_is_an_infrastructure_failure(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(DependencyInfraError):
        local(empty)(["pkg"], arch="amd64", exclude_newer=CUTOFF)


def test_the_resolver_identity_is_the_installed_uv_version() -> None:
    assert UvResolver().identity.startswith("uv@")
