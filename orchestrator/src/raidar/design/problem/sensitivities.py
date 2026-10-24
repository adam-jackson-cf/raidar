"""Known verifier sensitivities: ``registry/<dataset>/<problem>/<version>.sensitivities.yaml``.

An erratum (``raidar.design.problem.errata``) is a case the specification does not
determine; it changes what ``*_errata`` outcomes count. A *sensitivity* is the
opposite kind of note: the specification does determine the behaviour, but the
hidden test's verdict can depend on something besides the program's
behaviour, such as wall-clock timing or the random values it generates, so it
may pass a wrong program (or fail a right one) by chance. It is never an
erratum and never changes a value: it is a typed note the analysis shows beside
the grading-vs-coverage disagreement count (code-quality@3's
``regression_parity_excluded_cases``), so a reader can tell a flipped case that
is a known sensitivity from an unexplained one.

The file is committed beside the problem's manifest; it is not pinned by a lock
because nothing it says enters a value.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from raidar.design.problem.models import ProblemId
from raidar.design.problem.store import store_root

SENSITIVITIES_SUFFIX = ".sensitivities.yaml"
SCHEMA = "raidar.problem-sensitivities/1"


class SensitivityKind(StrEnum):
    """What besides the program the verdict can depend on."""

    TIMING = "timing"
    """Wall-clock timing (for example whether calls land in the same second)."""
    RANDOMNESS = "randomness"
    """Random values the program itself generates (for example uuid4 order on a tie)."""


class SensitivityError(ValueError):
    """The sensitivities file exists but is not valid."""


class VerifierSensitivity(BaseModel):
    """One hidden test case whose verdict can depend on more than the program."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case: str = Field(min_length=1, description="``<test file>::<test name>``")
    checkpoint: str = Field(min_length=1)
    kind: SensitivityKind
    spec_determined: bool = Field(
        description="True: the specification determines the behaviour (so it is no erratum)"
    )
    spec: str = Field(min_length=1, description="Where the specification determines it")
    mechanism: str = Field(min_length=1, description="How the verdict can depend on chance")
    recorded: str = Field(min_length=1, description="Date and where it was first recorded")


class SensitivitiesFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_: str = Field(alias="schema")
    problem: str
    sensitivities: tuple[VerifierSensitivity, ...] = Field(min_length=1)


def sensitivities_path(store_root: Path, problem: ProblemId) -> Path:
    return store_root / "registry" / problem.name / f"{problem.version}{SENSITIVITIES_SUFFIX}"


def load_sensitivities(path: Path, *, problem: str) -> tuple[VerifierSensitivity, ...]:
    """The file's sensitivities (empty when there is no file); invalid content raises."""
    if not path.is_file():
        return ()
    try:
        parsed = SensitivitiesFile.model_validate(yaml.safe_load(path.read_text()))
    except (yaml.YAMLError, ValidationError) as exc:
        raise SensitivityError(f"{path}: {exc}") from exc
    if parsed.schema_ != SCHEMA:
        raise SensitivityError(f"{path}: schema {parsed.schema_!r}, not {SCHEMA!r}")
    if parsed.problem != problem:
        raise SensitivityError(f"{path}: names problem {parsed.problem!r}, not {problem!r}")
    return parsed.sensitivities


def known_sensitivities(
    problems: Iterable[str], *, store: Path | None = None
) -> dict[str, tuple[VerifierSensitivity, ...]]:
    """``{problem ref: sensitivities}`` for the problems that have any (a missing file: none).

    ``store`` is the committed store root, resolved as ``raidar.design.problem.store.store_root``.
    """
    root = store_root(store)
    found = {}
    for ref in sorted(set(problems)):
        try:
            problem = ProblemId.parse(ref)
        except ValueError:  # not a registry reference (a local task): no notes
            continue
        notes = load_sensitivities(sensitivities_path(root, problem), problem=ref)
        if notes:
            found[ref] = notes
    return found


__all__ = [
    "SENSITIVITIES_SUFFIX",
    "SensitivityError",
    "SensitivityKind",
    "VerifierSensitivity",
    "known_sensitivities",
    "load_sensitivities",
    "sensitivities_path",
]
