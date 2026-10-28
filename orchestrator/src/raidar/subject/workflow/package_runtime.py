"""The proof a package arm needs before it is locked, and the trial's check against it.

``make stage-check`` runs a package's fixtures in a pack container and leaves two files under
``var/stage-checks/`` named by the package's ``stage-tree/1`` digest:

- ``<digest>.json``: the ``StageCheckRecord`` of the passing ``done`` fixture (canary baseline);
- ``<digest>.installed.json``: ``{arch: {tree_digest, wheels}}``, the installed tree
  (``install/1``'s digest of ``/opt/raidar/stages/<name>/``) that install produced per container
  architecture.

The lock folds in what those files say (``arm packages``): the expected installed-tree digest per
architecture and the digest of the check record. A trial then re-installs the package, and the
installed tree's digest must equal the *locked* one for the container's architecture; a missing
architecture, a digest that differs or a check record that is not the locked one makes the trial
invalid (instrument), never an agent outcome. A draft lock may lack the proof (a dev job that
never admits a measured run); a normal lock is refused without it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.contract.canonical import digest_json
from raidar.contract.paths import stage_checks_root
from raidar.subject.workflow.package_checks import ResolvedStage

INSTALLED_SUFFIX = ".installed.json"
CHECK_SUFFIX = ".json"


@dataclass(frozen=True)
class PackageEvidence:
    """What stage-check proved about one package digest, as the lock folds it."""

    installed: Mapping[str, str]
    """``{arch: installed tree digest}``."""
    check: str | None
    """Digest of the stage-check record (packages with a stop only)."""

    def entry(self) -> dict[str, Any]:
        found: dict[str, Any] = {"installed": dict(sorted(self.installed.items()))}
        if self.check is not None:
            found["check"] = self.check
        return found


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def installed_trees(digest: str, root: Path | None = None) -> dict[str, str]:
    """``{arch: tree digest}`` stage-check recorded for the package digest."""
    data = _read((root or stage_checks_root()) / f"{digest}{INSTALLED_SUFFIX}")
    if not isinstance(data, dict):
        return {}
    return {
        str(arch): str(item["tree_digest"])
        for arch, item in sorted(data.items())
        if isinstance(item, dict) and isinstance(item.get("tree_digest"), str)
    }


def check_record(digest: str, root: Path | None = None) -> dict[str, Any] | None:
    """The stored stage-check record JSON of the package digest, if present."""
    data = _read((root or stage_checks_root()) / f"{digest}{CHECK_SUFFIX}")
    return data if isinstance(data, dict) else None


def load_evidence(stage: ResolvedStage, root: Path | None = None) -> PackageEvidence:
    digest = stage.package.digest
    record = check_record(digest, root) if stage.package.manifest.on_stop else None
    return PackageEvidence(
        installed_trees(digest, root), None if record is None else digest_json(record)
    )


def evidence_issues(stage: ResolvedStage, evidence: PackageEvidence) -> list[str]:
    """What is missing for a normal lock of ``stage`` (empty: the proof is there)."""
    name = stage.release.name
    found = []
    if not evidence.installed:
        found.append(
            f"{stage.release}: no installed-tree record for {stage.package.digest[:12]}: "
            f"run `make stage-check NAME={name}`"
        )
    if stage.package.manifest.on_stop and evidence.check is None:
        found.append(
            f"{stage.release}: no stage-check record for {stage.package.digest[:12]}: "
            f"run `make stage-check NAME={name}`"
        )
    return found


def installed_mismatch(locked: Mapping[str, Any], arch: str, tree_digest: str) -> str | None:
    """Why a trial's installed tree is not the locked one, or ``None``.

    ``locked`` is the arm's ``packages[<name>]`` entry. An entry with no recorded architecture
    at all comes from a draft lock and is not compared.
    """
    expected = locked.get("installed") or {}
    if not expected:
        return None
    if arch not in expected:
        return (
            f"the lock records installed trees for {sorted(expected)}, none for {arch}: run "
            "`make stage-check` on that architecture and relock"
        )
    if expected[arch] != tree_digest:
        return (
            f"installed tree {tree_digest[:12]} differs from the locked {expected[arch][:12]} "
            f"for {arch}"
        )
    return None


def check_record_mismatch(
    locked: Mapping[str, Any], record: Mapping[str, Any] | None
) -> str | None:
    """Why the stage-check record a trial would use is not the locked one, or ``None``."""
    wanted = locked.get("check")
    if wanted is None:
        return None
    if record is None:
        return "the stage-check record is missing"
    if digest_json(record) != wanted:
        return "the stage-check record differs from the locked one"
    return None
