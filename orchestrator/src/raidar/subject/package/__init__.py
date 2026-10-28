"""Stage packages: strict manifests, tree digests and the pure package loader (``stage/1``)."""

from __future__ import annotations


class StagePackageError(ValueError):
    """A stage package, reference or tree is refused."""
