"""Load ``experiment.yaml`` with CLI overrides, following SCB precedence.

Precedence, highest first (SCB ``entrypoints/config/loader.py``):

1. CLI ``key=value`` overrides (``--set analysis.attempts=3``)
2. CLI flags (``--attempts``, ``--seed``); unset flags are ignored. Concurrency is
   per machine, so it is a plan/run option only, never a spec override: a lock
   built with it would not match the spec ``plan``/``run`` reload.
3. Values in the experiment file
4. Built-in schema defaults
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from raidar.design.experiment.canonical import sha256_hex
from raidar.design.experiment.metrics import DEFAULT_METRIC_REGISTRY, MetricRegistry
from raidar.design.experiment.schema import ExperimentSpec
from raidar.design.experiment.validation import (
    FieldIssue,
    SpecValidationError,
    issues_from_pydantic,
    semantic_issues,
)

FLAG_PATHS: Mapping[str, str] = {
    "attempts": "analysis.attempts",
    "seed": "analysis.seed",
}


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of keeping the last."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    keys: set[object] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node)
        if key in keys:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        keys.add(key)
    return loader.construct_mapping(node)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


@dataclass(frozen=True)
class CliOverrides:
    """CLI-supplied values: named flags (``FLAG_PATHS`` keys) and ``key=value`` sets."""

    flags: Mapping[str, object] = field(default_factory=dict)
    assignments: tuple[str, ...] = ()

    def effective_flags(self) -> dict[str, object]:
        return {name: value for name, value in sorted(self.flags.items()) if value is not None}

    def to_json(self) -> dict[str, object]:
        return {"flags": self.effective_flags(), "set": list(self.assignments)}


@dataclass(frozen=True)
class LoadedExperiment:
    spec: ExperimentSpec
    path: Path
    source_sha256: str
    overrides: CliOverrides

    @property
    def base_dir(self) -> Path:
        return self.path.parent


class _Unset:
    """Override value that removes its key (written as an empty ``key=``)."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()


def _typed_value(raw: str) -> object:
    """SCB ``parse_override`` typing: true/false, then int, then float, else str.

    Two additions: a value wrapped in matching quotes stays a string, so a
    numeric-looking string (``agents.0.version='1.10'``) can be set verbatim; and
    an empty value (``agents.0.reasoning_effort=``) is ``UNSET``, which removes the
    key so the field falls back to its schema default (``''`` sets an empty string).
    """
    if raw == "":
        return UNSET
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        return raw[1:-1]
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    for number in (int, float):
        try:
            return number(raw)
        except ValueError:
            continue
    return raw


def parse_assignment(text: str) -> tuple[str, object]:
    """Parse ``a.b.0=value`` with SCB's override typing (see ``_typed_value``)."""
    key, sep, raw = text.partition("=")
    key = key.strip()
    if not sep or not key:
        raise ValueError(f"invalid override {text!r}: expected key=value")
    return key, _typed_value(raw.strip())


def _step(container: object, part: str, path: str) -> object:
    if isinstance(container, dict):
        return container.setdefault(part, {})
    if isinstance(container, list) and part.isdigit() and int(part) < len(container):
        return container[int(part)]
    raise ValueError(f"override path {path!r} does not address a mapping key or list index")


def apply_assignment(document: dict[str, Any], path: str, value: object) -> None:
    parts = path.split(".")
    target: object = document
    for part in parts[:-1]:
        target = _step(target, part, path)
    last = parts[-1]
    if value is UNSET:
        if not isinstance(target, dict):
            raise ValueError(f"override path {path!r}: only a mapping key can be unset")
        target.pop(last, None)
    elif isinstance(target, dict):
        target[last] = value
    elif isinstance(target, list) and last.isdigit() and int(last) < len(target):
        target[int(last)] = value
    else:
        raise ValueError(f"override path {path!r} does not address a mapping key or list index")


def _unshared(node: Any) -> Any:
    """Copy containers without sharing (``copy.deepcopy`` keeps aliases shared).

    YAML anchors/aliases load as one Python object; a ``--set`` override must
    change only the node it addresses, so every container is made distinct.
    """
    if isinstance(node, dict):
        return {key: _unshared(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_unshared(value) for value in node]
    return node


def _read_document(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        document = yaml.load(raw.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise SpecValidationError(str(path), [FieldIssue("", f"unreadable YAML: {exc}")]) from exc
    if not isinstance(document, dict):
        raise SpecValidationError(
            str(path), [FieldIssue("", "experiment file must be a YAML mapping")]
        )
    return _unshared(document), sha256_hex(raw)


def _apply_overrides(document: dict[str, Any], overrides: CliOverrides) -> list[FieldIssue]:
    issues: list[FieldIssue] = []
    for name, value in overrides.effective_flags().items():
        if name not in FLAG_PATHS:
            issues.append(FieldIssue(f"--{name}", "unknown CLI flag"))
            continue
        apply_assignment(document, FLAG_PATHS[name], value)
    for text in overrides.assignments:
        try:
            key, value = parse_assignment(text)
            apply_assignment(document, key, value)
        except ValueError as exc:
            issues.append(FieldIssue(f"--set {text}", str(exc)))
    return issues


def _most_specific(issues: list[FieldIssue]) -> list[FieldIssue]:
    """Drop an issue when a deeper issue explains it (e.g. an empty-after-error tuple)."""
    return [
        issue
        for issue in issues
        if not any(
            other.loc.startswith(f"{issue.loc}.") for other in issues if issue.loc and other.loc
        )
    ]


def _validated(path: Path, document: dict[str, Any], **checks: Any) -> ExperimentSpec:
    try:
        spec = ExperimentSpec.model_validate(document)
    except ValidationError as exc:
        raise SpecValidationError(str(path), _most_specific(issues_from_pydantic(exc))) from exc
    issues = semantic_issues(spec, path=path, **checks)
    if issues:
        raise SpecValidationError(str(path), issues)
    return spec


def load_experiment(
    path: Path,
    overrides: CliOverrides | None = None,
    *,
    metrics: MetricRegistry = DEFAULT_METRIC_REGISTRY,
) -> LoadedExperiment:
    """Load, override, schema-validate and cross-check a schema v2 experiment file."""
    overrides = overrides or CliOverrides()
    path = path.resolve()
    document, source_sha256 = _read_document(path)
    override_issues = _apply_overrides(document, overrides)
    if override_issues:
        raise SpecValidationError(str(path), override_issues)
    spec = _validated(path, document, metrics=metrics)
    return LoadedExperiment(spec=spec, path=path, source_sha256=source_sha256, overrides=overrides)


def overrides_from_cli(
    *, attempts: int | None, seed: int | None, assignments: Sequence[str]
) -> CliOverrides:
    return CliOverrides(
        flags={"attempts": attempts, "seed": seed},
        assignments=tuple(assignments),
    )
