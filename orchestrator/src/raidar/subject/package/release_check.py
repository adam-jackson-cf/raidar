"""Stage release registration and committed evidence required by experiment locks."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from raidar_contract.canonical import digest_json
from raidar_contract.references import Release, ReleaseError

from raidar.contract.paths import lab_root, stage_checks_root, stages_root
from raidar.contract.releases import ReleaseKind, ReleaseRegister, Sha256, registration_issues
from raidar.subject.package.registry import load_stage_package
from raidar.subject.stages import stage_names
from raidar.subject.workflow.package_runtime import CHECK_SUFFIX, INSTALLED_SUFFIX


class _RecordedWheel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    sha256: Sha256


class _InstalledTree(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    tree_digest: Sha256
    wheels: list[_RecordedWheel]


class _LockedPackage(BaseModel):
    release: str
    sha256: Sha256
    installed: dict[str, Sha256]
    check: Sha256 | None = None


class _LockedArm(BaseModel):
    packages: dict[str, _LockedPackage] = Field(default_factory=dict)


class _LockedExecution(BaseModel):
    arms: dict[str, _LockedArm]


class _EvidenceLock(BaseModel):
    execution: _LockedExecution


def _check_record_issues(path: Path, digest: str, expected: str | None = None) -> list[str]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{path}: unusable stage-check record: {exc}"]
    if not isinstance(record, dict) or record.get("package") != digest:
        return [f"{path}: stage-check record has the wrong package identity (expected {digest})"]
    if expected is not None and digest_json(record) != expected:
        return [f"{path}: stage-check record differs from the lock"]
    return []


def _installed_record_issues(path: Path, expected: dict[str, str], label: str) -> list[str]:
    if not expected:
        return []
    try:
        installed = TypeAdapter(dict[str, _InstalledTree]).validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        return [f"{label}: unusable installed-tree record {path}: {exc}"]
    issues = []
    for arch, digest in expected.items():
        found = installed.get(arch)
        if found is None:
            issues.append(f"{label}: no installed-tree record for {arch} in {path}")
        elif found.tree_digest != digest:
            issues.append(f"{label}: installed-tree record for {arch} differs from locked {digest}")
    return issues


def _locked_evidence_issues(path: Path, root: Path, register: ReleaseRegister) -> list[str]:
    """Compare every architecture pinned by this lock, not the checking host's architecture."""
    lock = _EvidenceLock.model_validate_json(path.read_text(encoding="utf-8"))
    issues = []
    for arm, entry in lock.execution.arms.items():
        for name, package in entry.packages.items():
            label = f"{path}: arm {arm!r}, {name}"
            registered = register.of(ReleaseKind.STAGE).get(Release.parse_stage(package.release))
            if registered != package.sha256:
                issues.append(f"{label}: package digest {package.sha256} is not registered")
                continue
            installed_path = root / f"{package.sha256}{INSTALLED_SUFFIX}"
            issues += _installed_record_issues(installed_path, package.installed, label)
            if package.check is not None:
                check_path = root / f"{package.sha256}{CHECK_SUFFIX}"
                issues += _check_record_issues(check_path, package.sha256, package.check)
    return issues


def _stage_evidence_issues(repo_root: Path, register: ReleaseRegister) -> list[str]:
    """Committed records must name releases; committed locks must have usable evidence."""
    root = stage_checks_root(repo_root)
    registered = set(register.of(ReleaseKind.STAGE).values())
    issues = []
    for path in sorted(root.glob("*.json")):
        digest = path.name.removesuffix(INSTALLED_SUFFIX).removesuffix(CHECK_SUFFIX)
        if digest not in registered:
            issues.append(f"{path}: stage-check record names unregistered stage digest {digest}")
        elif not path.name.endswith(INSTALLED_SUFFIX):
            issues += _check_record_issues(path, digest)
    for path in sorted((lab_root(repo_root) / "experiments").glob("*/*/*.lock.json")):
        try:
            issues += _locked_evidence_issues(path, root, register)
        except (OSError, ValidationError, ReleaseError) as exc:
            issues.append(f"{path}: cannot check locked stage evidence: {exc}")
    return issues


def stage_release_issues(repo_root: Path, register: ReleaseRegister) -> list[str]:
    """Registration drift and unusable or unregistered stage-check evidence."""
    issues = registration_issues(
        ReleaseKind.STAGE,
        stage_names(repo_root),
        lambda name: load_stage_package(stages_root(repo_root) / name).digest,
        register,
    )
    return issues + _stage_evidence_issues(repo_root, register)
