"""Released stage packages: ``ns/name@N`` resolved against ``lab/releases.yaml``.

A package's hash is its ``stage-tree/1`` digest. Only the register's latest version of a name
resolves. ``ST1`` refuses an invalid package or an unsupported contract; ``ST4`` refuses bytes
that differ from the register. A draft resolves what the working tree holds (``subject.draft``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from raidar_contract.package import StagePackageError
from raidar_contract.package.manifest import StagePackage
from raidar_contract.package.registry import MANIFEST_FILE, load_stage_package, stage_folder
from raidar_contract.releases import (
    Release,
    ReleaseError,
    ReleaseKind,
    ReleaseRegister,
    changed_message,
    load_register,
    resolve_registered,
)
from raidar_contract.workflow import PackageWorkflowDefinition

from raidar.contract.paths import REPO_ROOT, releases_file, stages_root
from raidar.subject.draft import drafting
from raidar.subject.workflow.package_checks import ResolvedStage, package_issues


@dataclass(frozen=True)
class ReleasedStage:
    release: Release
    package: StagePackage

    @property
    def ref(self) -> str:
        return str(self.release)

    @property
    def digest(self) -> str:
        return self.package.digest


def released_stage(
    text: str,
    *,
    repo_root: Path = REPO_ROOT,
    register: ReleaseRegister | None = None,
    draft: bool | None = None,
) -> ReleasedStage:
    """Resolve ``text``; refuse invalid packages (ST1) or unregistered bytes (ST4)."""
    draft = drafting() if draft is None else draft
    register = register or load_register(releases_file(repo_root))
    release, registered = resolve_registered(register, ReleaseKind.STAGE, text, draft=draft)
    try:
        package = load_stage_package(stage_folder(release, repo_root))
    except (StagePackageError, OSError) as exc:
        hint = "; this Raidar supports stage/1" if "contract" in str(exc) else ""
        raise ReleaseError(f"ST1: {release}: {exc}{hint}") from exc
    if not draft and package.digest != registered:
        raise ReleaseError(f"ST4: {changed_message(release, str(registered), package.digest)}")
    return ReleasedStage(release, package)


def resolve_stages(
    definition: PackageWorkflowDefinition,
    repo_root: Path,
    register: ReleaseRegister,
    *,
    draft: bool,
) -> tuple[ResolvedStage, ...]:
    """Each package a workflow uses at its release, with effective params, ST-checked."""
    resolved = []
    for use in definition.stages:
        stage = released_stage(use.use, repo_root=repo_root, register=register, draft=draft)
        try:
            params = stage.package.effective_params(use.params)
        except StagePackageError as exc:
            raise ReleaseError(f"ST1: {use.use}: {exc}") from exc
        resolved.append(ResolvedStage(stage.release, stage.package, params))
    issues = package_issues(definition.build.prompt, resolved)
    if issues:
        raise ReleaseError(f"{definition.name}: " + "; ".join(issues))
    return tuple(resolved)


def stage_names(repo_root: Path = REPO_ROOT) -> list[str]:
    """Every package folder below ``lab/stages``: ``ns/name`` (sorted)."""
    root = stages_root(repo_root)
    if not root.is_dir():
        return []
    return sorted(
        f"{space.name}/{child.name}"
        for space in root.iterdir()
        if space.is_dir()
        for child in space.iterdir()
        if (child / MANIFEST_FILE).is_file()
    )
