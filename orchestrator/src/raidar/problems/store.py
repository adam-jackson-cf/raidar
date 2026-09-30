"""The problems store: committed pins and manifests, generated task content.

Layout (repo-root ``problems/``)::

    sources.yaml                               dataset pins (committed)
    registry/<dataset>/<problem>/<version>.json  ProblemManifest (committed)
    registry/.../<version>.validation.json     validation report (committed); a problem
                                               locks only under a usable verdict for
                                               its current manifest (F13)
    store/<dataset>/<problem>/<version>/       generated, gitignored:
        task/        agent-visible Harbor task (no hidden tests, no solutions)
        oracle/      host-only oracle bundle (hidden tests)
        references/  host-only reference payload per step (validation only)
    cache/                                     pinned dataset checkouts and
                                               infra-failed validation attempts, gitignored

``RAIDAR_PROBLEMS_ROOT`` overrides discovery; otherwise the first ancestor of
the start directory holding ``problems/sources.yaml`` wins.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from raidar.oracle.bundle import OracleBundle, load_bundle
from raidar.problems.content import content_digest, hidden_content_in_task
from raidar.problems.models import DatasetPin, ProblemId, ProblemManifest
from raidar.problems.validation import (
    ValidationRecord,
    ValidationRecordError,
    read_validation_record,
)

ROOT_ENV = "RAIDAR_PROBLEMS_ROOT"
STORE_DIR_NAME = "problems"
SOURCES_FILE = "sources.yaml"


class ProblemStoreError(ValueError):
    """The store is missing, malformed, or its content does not match a manifest."""


def find_store_root(start: Path) -> Path:
    override = os.environ.get(ROOT_ENV)
    if override:
        return Path(override).resolve()
    for directory in (start.resolve(), *start.resolve().parents):
        candidate = directory / STORE_DIR_NAME
        if (candidate / SOURCES_FILE).is_file():
            return candidate
    raise ProblemStoreError(
        f"no {STORE_DIR_NAME}/{SOURCES_FILE} above {start}; set {ROOT_ENV} to the problems store"
    )


@dataclass(frozen=True)
class ImportedProblem:
    """A manifest whose materialised content was verified against its digests."""

    manifest: ProblemManifest
    task_dir: Path
    bundle: OracleBundle
    references_dir: Path

    def reference(self, step: str) -> Path:
        return self.references_dir / step


@dataclass(frozen=True)
class ProblemStore:
    root: Path

    def sources(self) -> dict[str, DatasetPin]:
        path = self.root / SOURCES_FILE
        try:
            raw = yaml.safe_load(path.read_text()) or {}
            return {
                name: DatasetPin.model_validate(pin)
                for name, pin in (raw.get("sources") or {}).items()
            }
        except (OSError, yaml.YAMLError, ValidationError, AttributeError) as exc:
            raise ProblemStoreError(f"invalid {path}: {exc}") from exc

    def pin(self, dataset: str) -> DatasetPin:
        pins = self.sources()
        if dataset not in pins:
            raise ProblemStoreError(f"dataset {dataset!r} is not pinned in {SOURCES_FILE}")
        return pins[dataset]

    def manifest_path(self, problem: ProblemId) -> Path:
        return self.root / "registry" / problem.name / f"{problem.version}.json"

    def validation_path(self, problem: ProblemId) -> Path:
        return self.root / "registry" / problem.name / f"{problem.version}.validation.json"

    def validation_attempt_path(self, problem: ProblemId) -> Path:
        """Where a validation that hit infrastructure failures is written (not committed)."""
        return self.cache_dir() / "validation" / problem.name / f"{problem.version}.json"

    def content_dir(self, problem: ProblemId) -> Path:
        return self.root / "store" / problem.name / problem.version

    def cache_dir(self) -> Path:
        return self.root / "cache"

    def load_manifest(self, problem: ProblemId) -> ProblemManifest:
        path = self.manifest_path(problem)
        if not path.is_file():
            raise ProblemStoreError(
                f"problem {problem} is not imported (no {path}); run `raidar problem import`"
            )
        try:
            manifest = ProblemManifest.model_validate_json(path.read_text())
        except ValidationError as exc:
            raise ProblemStoreError(f"invalid manifest {path}: {exc}") from exc
        if manifest.ref != str(problem):
            raise ProblemStoreError(f"{path} records {manifest.ref}, expected {problem}")
        return manifest

    def write_manifest(self, manifest: ProblemManifest) -> Path:
        path = self.manifest_path(manifest.problem_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(manifest.model_dump_json(indent=2) + "\n")
        return path

    def load(self, problem: ProblemId) -> ImportedProblem:
        """Load an imported problem, refusing content that drifted from its manifest."""
        manifest = self.load_manifest(problem)
        content = self.content_dir(problem)
        task_dir, bundle_dir, references = (
            content / "task",
            content / "oracle",
            content / "references",
        )
        if not task_dir.is_dir() or not bundle_dir.is_dir():
            raise ProblemStoreError(
                f"problem {problem} is not materialised under {content}; "
                "run `raidar problem import` to regenerate it from the pinned source"
            )
        observed = {
            "task_sha256": content_digest(task_dir),
            "bundle_sha256": content_digest(bundle_dir),
            "references_sha256": content_digest(references),
        }
        drift = [
            f"{key}: manifest {getattr(manifest, key)[:12]} != store {value[:12]}"
            for key, value in observed.items()
            if getattr(manifest, key) != value
        ]
        if drift:
            raise ProblemStoreError(f"problem {problem} content drifted: {'; '.join(drift)}")
        bundle = load_bundle(bundle_dir)
        leaked = hidden_content_in_task(task_dir, bundle)
        if leaked:
            raise ProblemStoreError(
                f"problem {problem} task exposes hidden oracle content: {leaked}"
            )
        return ImportedProblem(
            manifest=manifest, task_dir=task_dir, bundle=bundle, references_dir=references
        )

    def require_validated(self, problem: ProblemId, manifest: ProblemManifest) -> ValidationRecord:
        """The recorded validation of ``manifest``, refusing any unusable verdict (F13)."""
        path = self.validation_path(problem)
        try:
            record = read_validation_record(path)
        except ValidationRecordError as exc:
            raise ProblemStoreError(
                f"problem {problem} is not oracle-validated ({exc}); run `raidar problem validate`"
            ) from exc
        if record.manifest_sha256 != manifest.digest():
            raise ProblemStoreError(
                f"problem {problem} validation report {path} is for manifest "
                f"{record.manifest_sha256[:12]}, not {manifest.digest()[:12]}; re-validate it"
            )
        if not record.usable:
            raise ProblemStoreError(
                f"problem {problem} is not usable: "
                f"validation verdict {record.verdict.value} ({path})"
            )
        return record
