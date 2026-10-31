"""A resolved package and its per-checkpoint lifecycle state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from raidar_contract.package.manifest import StagePackage
from raidar_contract.package.tokens import Scalar

from raidar.subject.primitive.canary import Canary, StageCheckRecord
from raidar.subject.primitive.install import InstalledPackage
from raidar.subject.primitive.setup import SetupResult
from raidar.subject.primitive.site import PackageSite
from raidar.subject.primitive.stop import StopState


@dataclass(frozen=True)
class PackageSpec:
    """A resolved, installed package with effective params and its trusted check record."""

    reference: str
    """The released ``<ns>/<name>``."""
    package: StagePackage
    installed: InstalledPackage
    params: Mapping[str, Scalar]
    expected: StageCheckRecord | None = None
    """The passing stage-check record; required when the package declares a stop."""


class PackageMember:
    """One package's per-checkpoint state."""

    def __init__(self, site: PackageSite, spec: PackageSpec) -> None:
        self.site = site
        self.spec = spec
        self.setup = SetupResult({}, {})
        self.stop = StopState()
        stop = spec.package.manifest.on_stop
        if stop is not None and spec.expected is None:
            raise ValueError(f"{spec.reference} has a stop but no stage-check record")
        self.canary = None if stop is None or spec.expected is None else Canary(site, spec.expected)
        self.outputs: dict[str, object] = {}

    @property
    def name(self) -> str:
        return self.spec.package.name
