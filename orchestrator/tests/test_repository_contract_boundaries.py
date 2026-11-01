"""Repository contract refusals: wheel admission, snapshots and resolver ownership."""

from __future__ import annotations

import stat
from importlib.metadata import EntryPoint
from pathlib import Path
from types import SimpleNamespace

import pytest

from raidar.contract import references, snapshot, wheel_cache
from raidar.contract.references import RefKind, RegistryReferenceError, ResolverSet
from raidar.contract.wheels import (
    LockedWheel,
    WheelPolicyError,
    generated_wheels_only_lock,
    pylock_wheels,
    uv_lock_wheels,
    wheel_fits,
)
from tests.experiment_spec_support import InMemoryResolver

_WHEEL = (
    '[[package]]\nname = "Example_Package"\nversion = "1.0"\n'
    'source = { registry = "https://pypi.org/simple" }\n'
    'wheels = [{ url = "https://files.pythonhosted.org/example_package-1.0-py3-none-any.whl", '
    f'hash = "sha256:{"a" * 64}", size = 7 }}]\n'
)


@pytest.mark.parametrize(
    ("lock", "reason"),
    [
        (_WHEEL.split("wheels =")[0], "no wheel"),
        (_WHEEL.replace("wheels = [", 'wheels = "invalid" # ['), "no wheel"),
        (_WHEEL.replace("url = ", "missing_url = "), "without a url"),
        (_WHEEL.replace("a" * 64, "not-a-digest"), "without a sha256"),
        (_WHEEL.replace("size = 7", 'size = "unknown"'), "invalid literal"),
        (
            _WHEEL.replace('source = { registry = "https://pypi.org/simple" }', ""),
            "no recorded source",
        ),
        (_WHEEL + _WHEEL.replace('version = "1.0"', 'version = "2.0"'), "more than one version"),
    ],
)
def test_stage_lock_refuses_incomplete_or_ambiguous_wheel_pins(lock: str, reason: str) -> None:
    with pytest.raises(WheelPolicyError, match=reason):
        uv_lock_wheels(lock, "amd64")


def test_generated_lock_does_not_admit_wheel_policy_failures() -> None:
    with pytest.raises(WheelPolicyError) as refused:
        generated_wheels_only_lock(_WHEEL.replace("a" * 64, "bad-hash"))

    assert "amd64:" in str(refused.value)
    assert "arm64:" in str(refused.value)
    assert "without a sha256" in str(refused.value)


def test_pylock_normalizes_names_and_exposes_the_installable_wheel_filename() -> None:
    lock = (
        '[[packages]]\nname = "Example_Package"\nversion = "1.0"\n'
        'wheels = [{url = "https://files.pythonhosted.org/example_package-1.0-py3-none-any.whl", '
        f'hashes = {{sha256 = "{"a" * 64}"}}, size = 7}}]\n'
    )

    (wheel,) = pylock_wheels(lock)

    assert wheel.name == "example-package"
    assert wheel.filename == "example_package-1.0-py3-none-any.whl"
    assert wheel_fits(wheel.filename, "amd64")
    assert wheel_fits(wheel.filename, "arm64")


@pytest.mark.parametrize("filename", ["not-a-wheel", "package.whl", "package-1.0.tar.gz"])
def test_malformed_wheel_filenames_cannot_match_a_pack(filename: str) -> None:
    assert not wheel_fits(filename, "amd64")
    assert not wheel_fits(filename, "arm64")


@pytest.mark.parametrize(
    "url",
    [
        "https://files.pythonhosted.org.evil.invalid/package.whl",
        "http://files.pythonhosted.org/package.whl",
        "file:///tmp/package.whl",
    ],
)
def test_wheel_cache_refuses_unapproved_origins_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setattr(wheel_cache, "open_wheel", lambda url: pytest.fail("untrusted download"))
    wheel = LockedWheel("package", "1.0", url, "a" * 64, 7)

    with pytest.raises(ValueError, match="not under an allowed host"):
        wheel_cache.fetch_locked([wheel], tmp_path, cache_root=tmp_path / "cache")

    assert list(tmp_path.iterdir()) == []


def test_snapshot_excludes_reserved_names_even_when_they_are_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path("/snapshot")
    monkeypatch.setattr(
        snapshot.os,
        "walk",
        lambda *args, **kwargs: [(str(root), [], [".git", "keep.py", ".raidar"])],
    )

    assert snapshot.snapshot_files(root) == [root / "keep.py"]


def test_snapshot_refuses_a_file_replaced_by_a_special_entry_after_the_path_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "source.py").write_text("print('safe')\n")
    monkeypatch.setattr(snapshot.os, "fstat", lambda fd: SimpleNamespace(st_mode=stat.S_IFIFO))

    with pytest.raises(OSError, match="not a regular file"):
        snapshot.tree_digest(tmp_path)


def test_resolver_kind_cannot_have_both_an_explicit_owner_and_an_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        references,
        "entry_points",
        lambda **kwargs: [EntryPoint("problem", "unused.module:Resolver", "fixture.resolvers")],
    )

    with pytest.raises(RegistryReferenceError, match="problem.*both an explicit resolver"):
        ResolverSet([InMemoryResolver(RefKind.PROBLEM)], entry_point_group="fixture.resolvers")
