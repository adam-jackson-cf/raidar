"""Scorer-owned verification plan contracts and normalization services."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raidar.scenario_paths import validate_relative_path
from raidar.schemas.environment import CapabilityRequirements
from raidar.schemas.scenario import _validate_argv_command

ACTION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
EVIDENCE_RUNNER_CONTRACT_VERSION = "python-evidence-runner@1"
PATH_ACTION_KINDS = {"read_file", "read_json", "archive"}
ACTION_KINDS = {"archive", "command", "glob", "read_file", "read_json", "visual_diff"}


class VerificationPlanError(ValueError):
    """Raised when scorer-owned verification plans are invalid."""


@dataclass(frozen=True, slots=True)
class VerificationAction:
    """One executable raw-evidence action declared by a scorer."""

    id: str
    kind: str
    timeout_sec: int | None = None
    required: bool = True
    depends_on: tuple[str, ...] = ()
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VerificationPlan:
    """Scorer-owned raw-evidence plan."""

    owner: str
    actions: tuple[VerificationAction, ...]


@dataclass(frozen=True, slots=True)
class VerificationPlanContext:
    """Resolved context used when a scorer declares evidence needs."""

    scenario: Any
    resolved_scorer: Any
    scenario_dir: Path
    environment_capabilities: CapabilityRequirements


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    """Raw evidence returned by the bundled evidence runner."""

    actions: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class NormalizedVerificationPlan:
    """Namespaced, de-duplicated verification plan for one run."""

    actions: tuple[VerificationAction, ...]
    aliases: dict[str, str]

    def payload(self) -> dict[str, Any]:
        """Return stable JSON-compatible runner input."""

        return {
            "schema_version": "1",
            "runner_contract": EVIDENCE_RUNNER_CONTRACT_VERSION,
            "actions": [_action_payload(action) for action in self.actions],
            "aliases": dict(sorted(self.aliases.items())),
        }

    def hash(self) -> str:
        """Return stable hash of the normalized plan."""

        encoded = json.dumps(self.payload(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def command(
    action_id: str,
    argv: list[str] | tuple[str, ...],
    *,
    timeout_sec: int | None = None,
    required: bool = True,
    depends_on: tuple[str, ...] = (),
) -> VerificationAction:
    """Construct a command action."""

    return VerificationAction(
        id=action_id,
        kind="command",
        timeout_sec=timeout_sec,
        required=required,
        depends_on=depends_on,
        config={"argv": list(argv)},
    )


def read_file(
    action_id: str,
    path: str,
    *,
    max_bytes: int | None = None,
    required: bool = True,
    depends_on: tuple[str, ...] = (),
) -> VerificationAction:
    """Construct a bounded text-file read action."""

    config: dict[str, Any] = {"path": path}
    if max_bytes is not None:
        config["max_bytes"] = max_bytes
    return VerificationAction(
        id=action_id,
        kind="read_file",
        required=required,
        depends_on=depends_on,
        config=config,
    )


def read_json(
    action_id: str,
    path: str,
    *,
    max_bytes: int | None = None,
    required: bool = True,
    depends_on: tuple[str, ...] = (),
) -> VerificationAction:
    """Construct a bounded JSON-file read action."""

    config: dict[str, Any] = {"path": path}
    if max_bytes is not None:
        config["max_bytes"] = max_bytes
    return VerificationAction(
        id=action_id,
        kind="read_json",
        required=required,
        depends_on=depends_on,
        config=config,
    )


def glob(
    action_id: str,
    pattern: str,
    *,
    required: bool = True,
    depends_on: tuple[str, ...] = (),
) -> VerificationAction:
    """Construct a workspace-relative glob action."""

    return VerificationAction(
        id=action_id,
        kind="glob",
        required=required,
        depends_on=depends_on,
        config={"pattern": pattern},
    )


def visual_diff(
    action_id: str,
    *,
    reference: str,
    actual: str,
    diff: str,
    regions: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    required: bool = True,
    depends_on: tuple[str, ...] = (),
) -> VerificationAction:
    """Construct a visual-diff raw evidence action."""

    return VerificationAction(
        id=action_id,
        kind="visual_diff",
        required=required,
        depends_on=depends_on,
        config={
            "reference": reference,
            "actual": actual,
            "diff": diff,
            "regions": list(regions),
        },
    )


def archive(
    action_id: str,
    path: str,
    *,
    required: bool = True,
    depends_on: tuple[str, ...] = (),
) -> VerificationAction:
    """Construct an archive/copy artifact action."""

    return VerificationAction(
        id=action_id,
        kind="archive",
        required=required,
        depends_on=depends_on,
        config={"path": path},
    )


def normalize_verification_plans(plans: tuple[VerificationPlan, ...]) -> NormalizedVerificationPlan:
    """Namespace, validate, de-duplicate, and topologically order scorer plans."""

    namespaced: list[tuple[str, VerificationAction]] = []
    owner_local_ids: set[str] = set()
    for plan in plans:
        _validate_id(plan.owner, "verification plan owner")
        local_ids = [action.id for action in plan.actions]
        if len(local_ids) != len(set(local_ids)):
            raise VerificationPlanError(f"verification plan {plan.owner} has duplicate action ids")
        for action in plan.actions:
            _validate_action(action)
            owner_local = f"{plan.owner}.{action.id}"
            if owner_local in owner_local_ids:
                raise VerificationPlanError(
                    f"duplicate verification action ownership: {owner_local}"
                )
            owner_local_ids.add(owner_local)
            namespaced.append((owner_local, _namespace_action(plan.owner, action)))

    aliases: dict[str, str] = {}
    actions_by_key: dict[str, VerificationAction] = {}
    canonical_by_id: dict[str, VerificationAction] = {}
    for owner_local, action in namespaced:
        key = _dedupe_key(action)
        canonical = actions_by_key.get(key)
        if canonical is None:
            actions_by_key[key] = action
            canonical_by_id[action.id] = action
            aliases[owner_local] = action.id
        else:
            aliases[owner_local] = canonical.id

    ordered = _topological_order(tuple(canonical_by_id.values()))
    return NormalizedVerificationPlan(actions=ordered, aliases=aliases)


def _namespace_action(owner: str, action: VerificationAction) -> VerificationAction:
    return VerificationAction(
        id=f"{owner}.{action.id}",
        kind=action.kind,
        timeout_sec=action.timeout_sec,
        required=action.required,
        depends_on=tuple(f"{owner}.{dependency}" for dependency in action.depends_on),
        config=dict(action.config),
    )


def _validate_action(action: VerificationAction) -> None:
    _validate_id(action.id, "verification action id")
    if action.kind not in ACTION_KINDS:
        raise VerificationPlanError(f"unsupported verification action kind: {action.kind}")
    if action.timeout_sec is not None and action.timeout_sec <= 0:
        raise VerificationPlanError(f"verification action {action.id} timeout_sec must be positive")
    for dependency in action.depends_on:
        _validate_id(dependency, f"verification action {action.id} dependency")
    _validate_action_config(action)


def _validate_action_config(action: VerificationAction) -> None:
    if action.kind == "command":
        argv = action.config.get("argv")
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise VerificationPlanError(f"verification action {action.id} command argv is required")
        try:
            _validate_argv_command(argv, field_name=f"verification action {action.id}.argv")
        except ValueError as exc:
            raise VerificationPlanError(str(exc)) from exc
    if action.kind in PATH_ACTION_KINDS:
        _validate_config_path(action, "path")
    if action.kind == "glob":
        _validate_config_path(action, "pattern")
    if action.kind == "visual_diff":
        for key in ("reference", "actual", "diff"):
            _validate_config_path(action, key)


def _validate_config_path(action: VerificationAction, key: str) -> None:
    value = action.config.get(key)
    if not isinstance(value, str):
        raise VerificationPlanError(f"verification action {action.id} config.{key} is required")
    try:
        validate_relative_path(
            value,
            field_name=f"verification action {action.id}.config.{key}",
            root_name="workspace",
        )
    except ValueError as exc:
        raise VerificationPlanError(str(exc)) from exc


def _validate_id(value: str, field_name: str) -> None:
    if not ACTION_ID_PATTERN.fullmatch(value):
        raise VerificationPlanError(
            f"{field_name} must contain only letters, numbers, dots, underscores, and hyphens"
        )


def _dedupe_key(action: VerificationAction) -> str:
    payload = {
        "kind": action.kind,
        "timeout_sec": action.timeout_sec,
        "required": action.required,
        "depends_on": sorted(action.depends_on),
        "config": action.config,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _topological_order(actions: tuple[VerificationAction, ...]) -> tuple[VerificationAction, ...]:
    by_id = {action.id: action for action in actions}
    for action in actions:
        missing = sorted(set(action.depends_on) - set(by_id))
        if missing:
            raise VerificationPlanError(
                f"verification action {action.id} depends on unknown actions: " + ", ".join(missing)
            )

    ordered: list[VerificationAction] = []
    temporary: set[str] = set()
    permanent: set[str] = set()

    def visit(action_id: str) -> None:
        if action_id in permanent:
            return
        if action_id in temporary:
            raise VerificationPlanError(f"verification action dependency cycle at {action_id}")
        temporary.add(action_id)
        for dependency in sorted(by_id[action_id].depends_on):
            visit(dependency)
        temporary.remove(action_id)
        permanent.add(action_id)
        ordered.append(by_id[action_id])

    for action_id in sorted(by_id):
        visit(action_id)
    return tuple(ordered)


def _action_payload(action: VerificationAction) -> dict[str, Any]:
    return {
        "id": action.id,
        "kind": action.kind,
        "timeout_sec": action.timeout_sec,
        "required": action.required,
        "depends_on": list(action.depends_on),
        "config": action.config,
    }
