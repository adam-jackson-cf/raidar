"""A trial installs each package once per container and holds every checkpoint to the lock."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raidar.contract.paths import stages_root
from raidar.contract.releases import Release
from raidar.subject.package.registry import load_stage_package
from raidar.subject.primitive.install import InstallError
from raidar.subject.primitive.site import TrialInvalid
from raidar.subject.workflow import package_trial
from raidar.subject.workflow.package_trial import install_locked
from tests.package_support import fixture_package

LOCKED_TREE = "t" * 64


def _setup(tmp_path: Path) -> tuple[Release, Any, dict[str, Any]]:
    package = load_stage_package(fixture_package("gate-check", tmp_path))
    locked = {"installed": {"arm64": LOCKED_TREE}}
    return Release.parse("gate-check@1"), package, locked


def _installed(digest: str) -> Any:
    return SimpleNamespace(digest=digest, record={"arch": "arm64"})


async def test_the_second_checkpoint_reuses_the_install_and_is_checked_against_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installs: list[str] = []
    present: list[Any] = []

    async def install(*_: Any, **kwargs: Any) -> Any:
        installs.append(kwargs["reference"])
        return _installed(LOCKED_TREE)

    async def reuse(*_: Any, **__: Any) -> Any:
        return present[0] if present else None

    monkeypatch.setattr(package_trial, "install_package", install)
    monkeypatch.setattr(package_trial, "reuse_installed", reuse)
    release, package, locked = _setup(tmp_path)

    first = await install_locked(object(), tmp_path, release, package, locked)
    present.append(_installed(LOCKED_TREE))  # the container now holds the install
    second = await install_locked(object(), tmp_path, release, package, locked)

    assert installs == ["gate-check"]  # installed once
    assert first.digest == second.digest == LOCKED_TREE


async def test_a_reused_tree_that_changed_is_invalid_never_repaired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def never(*_: Any, **__: Any) -> Any:
        raise AssertionError("a changed tree must not be reinstalled over")

    async def reuse(*_: Any, **__: Any) -> Any:
        return _installed("x" * 64)

    monkeypatch.setattr(package_trial, "install_package", never)
    monkeypatch.setattr(package_trial, "reuse_installed", reuse)
    release, package, locked = _setup(tmp_path)

    with pytest.raises(TrialInvalid, match="differs from the locked"):
        await install_locked(object(), tmp_path, release, package, locked)


async def test_an_unmeasurable_existing_install_is_an_integrity_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def reuse(*_: Any, **__: Any) -> Any:
        raise InstallError("installed tree: not root-owned")

    monkeypatch.setattr(package_trial, "reuse_installed", reuse)
    release, package, locked = _setup(tmp_path)

    with pytest.raises(TrialInvalid, match="integrity: gate-check@1: installed tree"):
        await install_locked(object(), tmp_path, release, package, locked)


def test_a_package_that_is_not_the_locked_bytes_is_invalid(tmp_path: Path) -> None:
    fixture_package("gate-check", stages_root(tmp_path))
    release = Release.parse("gate-check@1")

    with pytest.raises(TrialInvalid, match="is not in the lock's packages"):
        package_trial.load_locked_package(release, {}, tmp_path)
    with pytest.raises(TrialInvalid, match="the lock holds"):
        package_trial.load_locked_package(release, {"gate-check": {"sha256": "f" * 64}}, tmp_path)
