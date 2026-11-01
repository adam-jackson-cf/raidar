"""A trial holds each package it runs to the lock: bytes, installed tree and check record.

Every difference is an invalid instrument (``TrialInvalid``), never an agent outcome:

- the package on disk must hash to the locked ``stage-tree/1`` digest;
- the installed tree (``install/1``) must equal the locked digest for the container's
  architecture. A later checkpoint of the same trial reuses the earlier install after measuring
  it, so a changed tree is refused, never repaired or reinstalled over;
- the stage-check record must be the locked one (packages with a stop only).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from raidar_contract.package import StagePackageError
from raidar_contract.package.manifest import StagePackage
from raidar_contract.references import Release

from raidar.contract.paths import REPO_ROOT
from raidar.subject.package.registry import load_stage_package, stage_folder
from raidar.subject.primitive import calls
from raidar.subject.primitive.canary import StageCheckRecord
from raidar.subject.primitive.install import (
    InstalledPackage,
    InstallError,
    install_package,
    reuse_installed,
)
from raidar.subject.primitive.site import TrialInvalid
from raidar.subject.workflow.package_runtime import (
    check_record,
    check_record_mismatch,
    installed_mismatch,
)


def load_locked_package(
    release: Release, locked: Mapping[str, Mapping[str, Any]], repo_root: Path | None = None
) -> StagePackage:
    """The package at ``release`` from the tree, which must be exactly the locked bytes."""
    entry = locked.get(release.name)
    if entry is None:
        raise TrialInvalid("integrity", f"{release} is not in the lock's packages")
    folder = stage_folder(release, repo_root or REPO_ROOT)
    try:
        package = load_stage_package(folder)
    except (StagePackageError, OSError) as error:
        raise TrialInvalid("integrity", f"{release}: {error}") from error
    if package.digest != entry["sha256"]:
        raise TrialInvalid(
            "integrity",
            f"{release} is {package.digest[:12]} on disk, the lock holds "
            f"{str(entry['sha256'])[:12]}",
        )
    return package


async def install_locked(
    environment: Any,
    logs_dir: Path,
    release: Release,
    package: StagePackage,
    locked: Mapping[str, Any],
) -> InstalledPackage:
    """Install ``package`` once per container (or measure the earlier install) and hold the
    installed tree to the locked digest for the container's architecture."""
    tool = package.manifest.tool
    commands = tool.commands if tool else {}
    try:
        installed = await reuse_installed(
            environment,
            logs_dir,
            reference=release.name,
            files=package.files,
            package_digest=package.digest,
            commands=commands,
        ) or await install_package(
            environment,
            logs_dir,
            reference=release.name,
            package_digest=package.digest,
            files=package.files,
            commands=commands,
        )
    except InstallError as error:
        raise TrialInvalid("integrity", f"{release}: {error}") from error
    mismatch = installed_mismatch(locked, str(installed.record["arch"]), installed.digest)
    if mismatch is not None:
        raise TrialInvalid("integrity", f"{release}: {mismatch}", installed.record)
    return installed


def locked_check_record(
    release: Release, package: StagePackage, locked: Mapping[str, Any]
) -> StageCheckRecord | None:
    """The stage-check record of a package with a stop, which must be the locked one."""
    if package.manifest.on_stop is None:
        return None
    record = check_record(package.digest)
    mismatch = check_record_mismatch(locked, record)
    if mismatch is not None or record is None:
        raise TrialInvalid("instrument", f"{release}: {mismatch or 'no stage-check record'}")
    return StageCheckRecord.from_json(record)


async def place_skills(
    environment: Any, logs_dir: Path, package: StagePackage, placement: str
) -> list[dict[str, str]]:
    """Retain and place the installed package's skill files in the native agent's placement."""
    components = []
    for item in package.files:
        if not item.path.startswith("skills/"):
            continue
        relative = f"{placement}/{item.path.removeprefix('skills/')}"
        record = PurePosixPath("workflow", "components", package.name, relative)
        path = logs_dir / record
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(item.data)
        await calls.upload_file(environment, path, f"/app/{relative}")
        components.append({"path": relative, "sha256": item.sha256, "record": record.as_posix()})
    return components


async def verify_installed(packages: Sequence[InstalledPackage]) -> None:
    """Measure every installed tree again after the last turn."""
    for installed in packages:
        found = await installed.verify()
        if not found.ok:
            raise TrialInvalid(
                "integrity",
                f"{installed.reference}: the installed tree changed during the trial "
                f"({found.violation or found.digest[:12]})",
            )
