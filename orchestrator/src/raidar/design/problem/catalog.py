"""Select problem bindings from a pinned source or the committed registry."""

from __future__ import annotations

import subprocess
from pathlib import Path

from raidar.design.problem.models import ProblemId
from raidar.design.problem.source import PinnedCheckout, Runner, ensure_checkout, open_checkout
from raidar.design.problem.store import ProblemStore, ProblemStoreError


def source_catalog(
    store: ProblemStore,
    source: str,
    checkout_path: Path | None = None,
    *,
    run: Runner = subprocess.run,
) -> tuple[PinnedCheckout, tuple[ProblemId, ...]]:
    """List upstream problems only after verifying the requested source pin."""
    dataset, separator, revision = source.partition("@")
    pin = store.pin(dataset)
    if not separator or revision not in (pin.commit, pin.version):
        raise ProblemStoreError(f"source must match pinned {dataset}@{pin.commit}")
    checkout = (
        open_checkout(checkout_path, pin, run=run)
        if checkout_path is not None
        else ensure_checkout(store.cache_dir(), dataset, pin, run=run)
    )
    problems = tuple(
        ProblemId.parse(f"{dataset}/{path.parent.name}@{pin.version}")
        for path in sorted(checkout.root.glob("*/config.yaml"))
    )
    return checkout, problems


def select_problems(
    store: ProblemStore,
    refs: tuple[str, ...],
    names: str | None,
    source: str | None,
    checkout_path: Path | None = None,
    *,
    importing: bool = False,
    run: Runner = subprocess.run,
) -> tuple[ProblemId, ...]:
    """Resolve the single-reference and bulk forms without mixing their meanings."""
    if refs and (names is not None or source is not None):
        raise ProblemStoreError("use references or --problems/--source, not both")
    if refs:
        return tuple(dict.fromkeys(ProblemId.parse(ref) for ref in refs))
    if not names:
        raise ProblemStoreError("provide a problem reference or --problems")
    if importing and source is None:
        raise ProblemStoreError("bulk import requires --source <dataset>@<commit>")
    available = (
        source_catalog(store, source, checkout_path, run=run)[1]
        if source is not None
        else tuple(store.registered())
    )
    return _named_problems(available, names)


def _named_problems(available: tuple[ProblemId, ...], names: str) -> tuple[ProblemId, ...]:
    if names == "all":
        return available
    selected: list[ProblemId] = []
    for name in names.split(","):
        name = name.strip()
        matches = [
            problem
            for problem in available
            if name in (problem.name.split("/", 1)[-1], problem.name, str(problem))
        ]
        if len(matches) != 1:
            raise ProblemStoreError(f"problem {name!r} must identify one pinned problem")
        if matches[0] not in selected:
            selected.append(matches[0])
    return tuple(selected)
