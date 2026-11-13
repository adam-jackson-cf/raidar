"""Dependency readings of the graded submission: ``D1`` unprovided and ``D2`` undeclared imports.

Readings, not integrity checks: each is ``pass`` (with per-step evidence) or ``unmeasured``, and
never a violation, so neither can invalidate a trial (they are absent from
``raidar.engine.trial.collect.HEALTH_VALIDITY``). A step without the evidence a reading needs
is listed under ``unmeasured`` in the evidence, never counted as unaffected.

- ``D1`` per step, the declared requirements Raidar could not provide, as
  ``{requirement, reason}`` (the step record's ``submission_dependencies.unprovided``).
- ``D2`` per step, the top-level modules of ``ModuleNotFoundError`` lines in the grader log that
  are neither stdlib, nor defined by the step's stored snapshot, nor named like a declared
  distribution. Each finding is ``{module, basis}``. Raidar holds no distribution-to-import
  metadata, so the name match is a heuristic: ``basis`` is ``confirmed`` only when the snapshot
  was checked and declares nothing at all, else ``heuristic`` (``yaml`` may come from a declared
  ``PyYAML``). Neither basis names a distribution.

Evidence of both: ``{"steps": {step: [...]}, "unmeasured": [step, ...]}``.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Collection, Iterable
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from raidar.contract.file_io import load_text
from raidar.engine.health.base import Audit, make_check
from raidar.engine.health.model import PASS, UNMEASURED, HealthCheck
from raidar.grading.records import step_record_dir
from raidar.grading.submission_deps import SubmissionDependencies

GRADER_LOG = "grading.log"
_MISSING_MODULE = re.compile(r"ModuleNotFoundError: No module named ['\"]([^'\"]+)['\"]")


def _distribution_names(declared: Iterable[str]) -> set[str]:
    names: set[str] = set()
    for text in declared:
        try:
            names.add(canonicalize_name(Requirement(text).name))
        except InvalidRequirement:
            continue
    return names


def local_modules(tree: Path) -> set[str]:
    """Top-level module names the snapshot itself defines (at its root or under ``src/``)."""
    names: set[str] = set()
    for root in (tree, tree / "src"):
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if entry.is_dir():
                names.add(entry.name)
            elif entry.suffix == ".py":
                names.add(entry.stem)
    return names


def undeclared_modules(
    log: str, declared: Iterable[str], local: Collection[str] | None = None
) -> list[dict[str, str]]:
    """Modules the log failed to import that are not stdlib, local, or named like a declaration.

    ``basis`` is ``confirmed`` only when nothing could supply the module: the snapshot was
    checked for local modules and declares no distribution at all. Otherwise it is
    ``heuristic``: the name matches no declared distribution, but Raidar holds no
    distribution-to-import metadata, so an alias (``yaml`` from ``PyYAML``) is not excluded.
    """
    declared = tuple(declared)
    distributions = _distribution_names(declared)
    found = {match.group(1).split(".")[0] for match in _MISSING_MODULE.finditer(log)}
    basis = "confirmed" if local is not None and not declared else "heuristic"
    return [
        {"module": module, "basis": basis}
        for module in sorted(found)
        if module not in sys.stdlib_module_names
        and module not in (local or ())
        and canonicalize_name(module) not in distributions
    ]


def _dependencies(a: Audit, step: str) -> SubmissionDependencies | None:
    ledger = a.ledger(step)
    return None if ledger is None else ledger.submission_dependencies


def _unprovided(a: Audit, step: str) -> list[dict[str, str]] | None:
    dependencies = _dependencies(a, step)
    if dependencies is None:
        return None
    return [{"requirement": u.requirement, "reason": u.reason} for u in dependencies.unprovided]


def _local(a: Audit, stored_path: str) -> set[str] | None:
    """The snapshot's own module names; ``None`` when the stored tree is not there to read."""
    tree = a.trial_dir / stored_path
    return local_modules(tree) if tree.is_dir() else None


def _undeclared(a: Audit, step: str) -> list[dict[str, str]] | None:
    ledger = a.ledger(step)
    dependencies = _dependencies(a, step)
    log = step_record_dir(a.trial_dir, step) / GRADER_LOG
    if ledger is None or dependencies is None or not log.is_file():
        return None
    try:
        text = load_text(log, errors="replace")
    except OSError:
        return None
    return undeclared_modules(text, dependencies.declared, _local(a, ledger.snapshot.stored_path))


def _reading(
    check_id: str, a: Audit, read: Callable[[Audit, str], Any], noun: str, affected: str
) -> HealthCheck:
    steps: dict[str, Any] = {}
    unmeasured: list[str] = []
    for item in a.attempted:
        value = read(a, item.step)
        if value is None:
            unmeasured.append(item.step)
        else:
            steps[item.step] = value
    if not steps:
        reason = f"no graded step recorded {noun}" if a.attempted else "no step was attempted"
        return make_check(check_id, UNMEASURED, reason, steps={}, unmeasured=unmeasured)
    hit = [step for step, value in steps.items() if value]
    detail = f"{len(hit)} of {len(steps)} measured step(s) {affected}"
    if unmeasured:
        detail += f"; {len(unmeasured)} step(s) unmeasured"
    return make_check(check_id, PASS, detail, steps=steps, unmeasured=unmeasured)


def check_d1(a: Audit) -> HealthCheck:
    return _reading(
        "D1", a, _unprovided, "its dependency evidence", "had a declared requirement unprovided"
    )


def check_d2(a: Audit) -> HealthCheck:
    return _reading(
        "D2",
        a,
        _undeclared,
        "its dependency evidence and grader log",
        "failed to import a non-stdlib, non-local module no declared name matches "
        "(heuristic unless basis is confirmed)",
    )
