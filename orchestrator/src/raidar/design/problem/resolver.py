"""``RegistryResolver`` for kind ``problem`` (entry point ``raidar.design.experiment.resolvers``).

Resolution reads only committed records - the manifest and its validation
report - so ``raidar lock`` works without materialised task content (and
without Docker). The digest is the manifest's canonical digest, which covers
the pinned source commit, git tree ids and the content digests of the task,
verifier bundle and references.

A problem resolves only after verifier validation (F13; plan task 0.5: "any
failing problem is excluded"): its report must be for the current manifest and
carry a usable verdict. Excluded, infra-failed, stale or missing validation is
reported to the compatibility gate (``binding.validated``), which refuses the
lock with ``RT0``; a validated problem's lock records the verdict and report
digest.

A problem with a committed errata (``<version>.errata.yaml``) also records it
under ``metadata.errata``: the sha256 of the file and its case ids by
disposition. The metadata is part of the execution section, so registering or
changing an errata changes ``lock_sha256``: the verifier records
errata-applied verdicts, and jobs say which errata they ran under. An errata
for another manifest refuses the reference.

A problem reference names a *binding*: one upstream problem imported against
one runtime pack (``scb/...`` is the python binding, ``scb-node-ts/...`` would be
the node-ts one). The manifest's ``pack`` is authoritative and reaches the gate
through ``binding`` only, so the recorded lock metadata is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from raidar.design.experiment.references import (
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    ResolvedReference,
)
from raidar.design.problem.models import ProblemId
from raidar.design.problem.store import ProblemStore, ProblemStoreError, open_store


@dataclass(frozen=True)
class ProblemResolver:
    """Resolve ``<dataset>/<problem>[.<checkpoint>]@<version>`` against a problems store.

    ``store_root`` pins the store; when unset the store is discovered from the
    experiment's directory (see ``raidar.design.problem.store.open_store``).
    """

    store_root: Path | None = None

    @property
    def kind(self) -> RefKind:
        return RefKind.PROBLEM

    def resolve(self, ref: RegistryRef, *, base_dir: Path) -> ResolvedReference:
        problem = ProblemId.from_ref(ref)
        try:
            store = ProblemStore(self.store_root) if self.store_root else open_store(base_dir)
            manifest = store.load_manifest(problem)
            errata = store.load_errata(problem, manifest)
        except ProblemStoreError as exc:
            raise RegistryReferenceError(str(exc)) from exc
        binding: dict[str, object] = {
            "runtime": manifest.pack.id,
            "pack_version": manifest.pack.version,
        }
        try:
            validation = store.require_validated(problem, manifest)
        except ProblemStoreError as exc:
            return ResolvedReference(
                ref=ref,
                digest=manifest.digest(),
                metadata=manifest.lock_metadata(),
                binding={**binding, "validated": False, "validation_issue": str(exc)},
            )
        metadata = {**manifest.lock_metadata(), "validation": validation.lock_metadata()}
        if errata is not None:
            metadata["errata"] = errata.lock_metadata()
        return ResolvedReference(
            ref=ref,
            digest=manifest.digest(),
            metadata=metadata,
            binding={**binding, "validated": True},
        )


def resolver_factory() -> ProblemResolver:
    """Zero-argument factory for the ``raidar.design.experiment.resolvers`` entry point."""
    return ProblemResolver()
