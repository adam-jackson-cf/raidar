"""Problem registry records: pinned dataset sources and imported-problem manifests.

A problem reference is ``<dataset>/<problem>[.<checkpoint>]@<version>``:

- ``scb/forge@38d627ecf668``: every checkpoint of SCB ``forge`` as one
  multi-step task;
- ``scb/circuit_eval.checkpoint_1@38d627ecf668``: the first checkpoint alone
  as a one-shot (1-step) task.

``version`` is the first 12 hex characters of the pinned dataset commit, so a
reference can never silently float to other content. The manifest is the
committed record of one import; the materialised task, verifier bundle and
reference payloads it describes are regenerated from the pinned source and
must reproduce the recorded digests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from raidar.spec.canonical import digest_json
from raidar.spec.references import RefKind, RegistryRef, RegistryReferenceError

VERSION_CHARS = 12
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_PROBLEM_NAME = re.compile(
    r"^(?P<dataset>[a-z0-9-]+)/(?P<problem>[a-z0-9_-]+)(\.(?P<cp>[a-z0-9_]+))?$"
)


class ProblemRefError(RegistryReferenceError):
    """A problem reference is malformed for the problem registry."""


class ProblemMode(StrEnum):
    MULTI_CHECKPOINT = "multi_checkpoint"
    ONE_SHOT = "one_shot"


class ProblemModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourcePin(ProblemModel):
    """A source pinned to one git commit and bound to one runtime (``problems/sources.yaml``).

    A source key is the binding's dataset segment: ``scb`` is the python
    binding of the SCB problems, a ``scb-node-ts`` key would bind the same
    upstream to the node-ts pack (Terminal-Bench's variant-suffix convention).
    The importer refuses a pack whose id is not the source's ``runtime``.
    """

    format: Literal["scb-problems"] = "scb-problems"
    url: str = Field(min_length=1)
    commit: str = Field(pattern=_COMMIT.pattern)
    converter: str = "scripts/scb_to_harbor.py"
    runtime: str = "python"

    @property
    def version(self) -> str:
        return self.commit[:VERSION_CHARS]


@dataclass(frozen=True)
class ProblemId:
    """A parsed problem reference."""

    dataset: str
    problem: str
    checkpoint: str | None
    version: str

    @classmethod
    def parse(cls, text: str) -> ProblemId:
        return cls.from_ref(RegistryRef.parse(RefKind.PROBLEM, text))

    @classmethod
    def from_ref(cls, ref: RegistryRef) -> ProblemId:
        match = _PROBLEM_NAME.fullmatch(ref.name)
        if match is None:
            raise ProblemRefError(
                f"problem reference {ref} must be <dataset>/<problem>[.<checkpoint>]@<version>"
            )
        return cls(
            dataset=match.group("dataset"),
            problem=match.group("problem"),
            checkpoint=match.group("cp"),
            version=ref.version,
        )

    @property
    def name(self) -> str:
        suffix = f".{self.checkpoint}" if self.checkpoint else ""
        return f"{self.dataset}/{self.problem}{suffix}"

    @property
    def mode(self) -> ProblemMode:
        return ProblemMode.ONE_SHOT if self.checkpoint else ProblemMode.MULTI_CHECKPOINT

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


class ProblemSource(ProblemModel):
    """Where the task came from, down to git tree ids and converter output."""

    dataset: str
    url: str
    commit: str = Field(pattern=_COMMIT.pattern)
    problem: str
    checkpoint_selection: str | None = None
    problem_tree: str = Field(min_length=1, description="git tree id of <problem>/")
    converter_tree: str = Field(
        min_length=1, description="git tree id of the converter's directory (scripts/)"
    )
    conversion_overrides: str | None = Field(
        default=None, description="git blob id of conversion.toml, when present"
    )
    converted_task_sha256: str = Field(min_length=1)


class ProblemStep(ProblemModel):
    name: str = Field(min_length=1)
    checkpoint: str = Field(min_length=1)
    instruction_sha256: str = Field(min_length=1)
    test_files: tuple[str, ...] = Field(min_length=1)


class PackBinding(ProblemModel):
    id: str
    version: int
    image: str


class ProblemManifest(ProblemModel):
    """Committed record of one import (``problems/registry/<name>/<version>.json``)."""

    kind: Literal["raidar-problem"] = "raidar-problem"
    schema_version: Literal[1] = 1
    ref: str
    mode: ProblemMode
    checkpoints: int = Field(ge=1)
    steps: tuple[ProblemStep, ...] = Field(min_length=1)
    source: ProblemSource
    pack: PackBinding
    task_sha256: str
    bundle_sha256: str
    references_sha256: str
    # Snapshot-relative entrypoint: the SCB config ``entry_file`` in the pack's
    # format (``forge`` -> ``forge.py``). Scorers take it as a config default.
    entry_file: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> ProblemManifest:
        problem_id = ProblemId.parse(self.ref)
        if len(self.steps) != self.checkpoints:
            raise ValueError(f"{self.checkpoints} checkpoints but {len(self.steps)} steps")
        if problem_id.mode is not self.mode:
            raise ValueError(f"reference {self.ref} implies mode {problem_id.mode.value}")
        if self.mode is ProblemMode.ONE_SHOT and self.checkpoints != 1:
            raise ValueError("a one-shot problem has exactly one checkpoint")
        if not self.source.commit.startswith(problem_id.version):
            raise ValueError(f"reference version {problem_id.version} != source commit")
        return self

    @property
    def problem_id(self) -> ProblemId:
        return ProblemId.parse(self.ref)

    def payload(self) -> dict[str, object]:
        """JSON form; a manifest without ``entry_file`` keeps its earlier bytes and digest."""
        dumped = self.model_dump(mode="json")
        if self.entry_file is None:
            dumped.pop("entry_file")
        return dumped

    def digest(self) -> str:
        return digest_json(self.payload())

    def lock_metadata(self) -> dict[str, object]:
        """Deterministic metadata recorded by ``raidar lock`` (checkpoints is required)."""
        metadata: dict[str, object] = {
            "checkpoints": self.checkpoints,
            "mode": self.mode.value,
            "steps": [step.name for step in self.steps],
            "source": {
                "dataset": self.source.dataset,
                "url": self.source.url,
                "commit": self.source.commit,
                "problem": self.source.problem,
            },
            "pack_image": self.pack.image,
            "task_sha256": self.task_sha256,
            "bundle_sha256": self.bundle_sha256,
        }
        if self.entry_file is not None:
            metadata["entry_file"] = self.entry_file
        return metadata
