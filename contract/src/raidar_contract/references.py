"""Immutable release-reference grammar, independent of registries and repository layout."""

from __future__ import annotations

import re
from dataclasses import dataclass

RELEASE_PATTERN = r"^(?:[a-z0-9][a-z0-9-]*/)?[a-z0-9][a-z0-9-]*@[1-9][0-9]*$"
STAGE_RELEASE_PATTERN = r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*@[1-9][0-9]*$"
WORKFLOW_RELEASE_PATTERN = STAGE_RELEASE_PATTERN
_RELEASE_RE = re.compile(RELEASE_PATTERN)
_STAGE_RELEASE_RE = re.compile(STAGE_RELEASE_PATTERN)
_WORKFLOW_RELEASE_RE = re.compile(WORKFLOW_RELEASE_PATTERN)


class ReleaseError(ValueError):
    """A release reference is malformed, unregistered or not the one the tree holds."""


@dataclass(frozen=True, order=True)
class Release:
    """A parsed immutable release reference: ``name@N``, or ``ns/name@N`` for a stage."""

    name: str
    version: int

    @classmethod
    def parse(cls, text: str) -> Release:
        if not _RELEASE_RE.fullmatch(text):
            raise ReleaseError(f"invalid release {text!r}: expected <name>@<N> (N from 1)")
        name, version = text.split("@", 1)
        return cls(name, int(version))

    @classmethod
    def parse_stage(cls, text: str) -> Release:
        """A stage reference: the namespace is required (``<ns>/<name>@<N>``)."""
        if not _STAGE_RELEASE_RE.fullmatch(text):
            raise ReleaseError(
                f"invalid stage release {text!r}: expected <ns>/<name>@<N> "
                "(a namespace is required; N from 1)"
            )
        return cls.parse(text)

    @classmethod
    def parse_workflow(cls, text: str) -> Release:
        """A workflow reference: the namespace is required (``<ns>/<name>@<N>``)."""
        if not _WORKFLOW_RELEASE_RE.fullmatch(text):
            raise ReleaseError(
                f"invalid workflow release {text!r}: expected <ns>/<name>@<N> "
                "(a namespace is required; N from 1)"
            )
        return cls.parse(text)

    @property
    def leaf(self) -> str:
        return self.name.rsplit("/", 1)[-1]

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"
