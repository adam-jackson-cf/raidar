"""``RegistryResolver`` for kind ``problem`` (entry point ``raidar.spec.resolvers``).

Resolution reads only committed records - the manifest and its validation
report - so ``raidar lock`` works without materialised task content (and
without Docker). The digest is the manifest's canonical digest, which covers
the pinned source commit, git tree ids and the content digests of the task,
oracle bundle and references.

A problem resolves only after oracle validation (F13; plan task 0.5: "any
failing problem is excluded"): its report must be for the current manifest and
carry a usable verdict. Excluded, infra-failed, stale or missing validation is
a resolution error, and the lock records the verdict and report digest.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from raidar.problems.models import ProblemId
from raidar.problems.store import ProblemStore, ProblemStoreError, find_store_root
from raidar.spec.references import (
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    ResolvedReference,
)


@dataclass(frozen=True)
class ProblemResolver:
    """Resolve ``<dataset>/<problem>[.<checkpoint>]@<version>`` against a problems store.

    ``store_root`` pins the store; when unset the store is discovered from the
    experiment's directory (see ``raidar.problems.store.find_store_root``).
    """

    store_root: Path | None = None

    @property
    def kind(self) -> RefKind:
        return RefKind.PROBLEM

    def resolve(self, ref: RegistryRef, *, base_dir: Path) -> ResolvedReference:
        problem = ProblemId.from_ref(ref)
        try:
            store = ProblemStore(self.store_root or find_store_root(base_dir))
            manifest = store.load_manifest(problem)
            validation = store.require_validated(problem, manifest)
        except ProblemStoreError as exc:
            raise RegistryReferenceError(str(exc)) from exc
        metadata = {**manifest.lock_metadata(), "validation": validation.lock_metadata()}
        return ResolvedReference(ref=ref, digest=manifest.digest(), metadata=metadata)


def resolver_factory() -> ProblemResolver:
    """Zero-argument factory for the ``raidar.spec.resolvers`` entry point."""
    return ProblemResolver()
