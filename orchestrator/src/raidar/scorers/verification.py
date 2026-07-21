"""Scorer-owned verification plan contracts and normalization services."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SkipValidation, model_validator

from raidar.scenario_paths import validate_relative_path
from raidar.schemas.scenario import _validate_argv_command

ACTION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
EVIDENCE_SCHEMA_VERSION = "1"
EVIDENCE_RUNNER_CONTRACT_VERSION = "python-evidence-runner@2"
ACTION_KINDS = {
    "archive",
    "command",
    "glob",
    "public_contract_snapshot",
    "read_file",
    "read_json",
    "structural_snapshot",
    "visual_diff",
}
DEFAULT_SCORER_COMMAND_TIMEOUT_SEC = 60


class VerificationPlanError(ValueError):
    """Raised when scorer-owned verification plans are invalid."""


class VerificationEvidenceError(ValueError):
    """Raised when current-version evidence violates its contract."""


class EvidenceDependency(BaseModel):
    """Typed evidence dependency declared by a scorer definition."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal[
        "archive",
        "command",
        "glob",
        "public_contract_snapshot",
        "read_file",
        "read_json",
        "structural_snapshot",
        "visual_diff",
    ]
    minimum: int = Field(default=1, ge=1)


class ArchiveActionConfig(BaseModel):
    """Resolved archive action configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str
    output: str


class CommandActionConfig(BaseModel):
    """Resolved command action configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    argv: list[str] = Field(min_length=1)
    cwd: str | None = None


class GlobActionConfig(BaseModel):
    """Resolved glob action configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    pattern: str


class ReadActionConfig(BaseModel):
    """Resolved bounded-read action configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str
    max_bytes: int | None = Field(default=None, gt=0)


class SnapshotActionConfig(BaseModel):
    """Resolved source-surface snapshot configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    paths: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_paths(self) -> SnapshotActionConfig:
        if len(self.paths) != len(set(self.paths)):
            raise ValueError("snapshot paths must be unique")
        for index, path in enumerate(self.paths):
            validate_relative_path(
                path,
                field_name=f"snapshot paths[{index}]",
                root_name="workspace",
            )
        return self


class VisualRegionActionConfig(BaseModel):
    """Resolved visual-region comparison configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str
    reference_image: str
    actual_image: str
    diff_image: str
    weight: float = Field(gt=0)


class VisualDiffActionConfig(BaseModel):
    """Resolved visual comparison configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    reference: str
    actual: str
    diff: str
    argv_template: list[str] = Field(min_length=1)
    threshold: float = Field(ge=0, le=1)
    regions: list[VisualRegionActionConfig]


EvidenceActionConfig = (
    ArchiveActionConfig
    | CommandActionConfig
    | GlobActionConfig
    | ReadActionConfig
    | SnapshotActionConfig
    | VisualDiffActionConfig
)


class EvidencePlanAction(BaseModel):
    """Strict serialized runner action."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str
    kind: Literal[
        "archive",
        "command",
        "glob",
        "public_contract_snapshot",
        "read_file",
        "read_json",
        "structural_snapshot",
        "visual_diff",
    ]
    timeout_sec: int | None
    required: bool
    depends_on: list[str]
    config: SkipValidation[EvidenceActionConfig]

    @model_validator(mode="after")
    def _validate_config_shape(self) -> EvidencePlanAction:
        expected = {
            "archive": ArchiveActionConfig,
            "command": CommandActionConfig,
            "glob": GlobActionConfig,
            "public_contract_snapshot": SnapshotActionConfig,
            "read_file": ReadActionConfig,
            "read_json": ReadActionConfig,
            "structural_snapshot": SnapshotActionConfig,
            "visual_diff": VisualDiffActionConfig,
        }[self.kind]
        try:
            validated_config = expected.model_validate(self.config)
        except ValueError as exc:
            raise ValueError(f"evidence action {self.id} has invalid {self.kind} config") from exc
        if self.kind in {"command", "visual_diff"} and self.timeout_sec is None:
            raise ValueError(f"evidence action {self.id} timeout_sec is required")
        object.__setattr__(self, "config", validated_config)
        return self


class EvidenceRunnerLimits(BaseModel):
    """Resolved bounded-I/O limits consumed by the evidence runner."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    file_bytes: int = Field(gt=0)
    text_chars: int = Field(gt=0)
    list_items: int = Field(gt=0)
    list_item_chars: int = Field(gt=0)


class EvidencePlanInput(BaseModel):
    """Strict current-version input accepted by the bundled runner."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1"]
    runner_contract: Literal["python-evidence-runner@2"]
    workspace: str
    output_dir: str
    actions: list[EvidencePlanAction]
    aliases: dict[str, str]
    limits: EvidenceRunnerLimits
    archive_excludes: list[str]

    @model_validator(mode="after")
    def _validate_action_graph(self) -> EvidencePlanInput:
        action_ids = [action.id for action in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("evidence plan action ids must be unique")
        unknown_aliases = sorted(set(self.aliases.values()) - set(action_ids))
        if unknown_aliases:
            raise ValueError(
                "evidence plan aliases reference unknown actions: " + ", ".join(unknown_aliases)
            )
        for action in self.actions:
            unknown_dependencies = sorted(set(action.depends_on) - set(action_ids))
            if unknown_dependencies:
                raise ValueError(
                    f"evidence action {action.id} has unknown dependencies: "
                    + ", ".join(unknown_dependencies)
                )
        return self


@dataclass(frozen=True, slots=True)
class VerificationAction:
    """One executable raw-evidence action declared by a scorer."""

    id: str
    kind: str
    timeout_sec: int | None = None
    required: bool = True
    depends_on: tuple[str, ...] = ()
    config: dict[str, Any] = field(default_factory=dict)


def _validate_visual_comparison_command(
    executable: object,
    arguments_template: object,
) -> None:
    if not isinstance(executable, str) or not executable:
        raise TypeError("visual comparison executable must be a non-empty string")
    if not isinstance(arguments_template, tuple) or not all(
        isinstance(argument, str) and argument for argument in arguments_template
    ):
        raise TypeError("visual comparison arguments_template must be a tuple of non-empty strings")


def _validate_visual_comparison_placeholders(arguments_template: tuple[str, ...]) -> None:
    required_placeholders = {"{reference}", "{actual}", "{diff}", "{threshold}"}
    missing = required_placeholders - set(arguments_template)
    if missing:
        raise ValueError(
            "visual comparison arguments_template is missing placeholders: "
            + ", ".join(sorted(missing))
        )


def _validate_visual_comparison_threshold(threshold: object) -> None:
    if (
        not isinstance(threshold, int | float)
        or isinstance(threshold, bool)
        or not 0 <= float(threshold) <= 1
    ):
        raise ValueError("visual comparison threshold must be between zero and one")


def _validate_visual_comparison_timeout(timeout_sec: object) -> None:
    if not isinstance(timeout_sec, int) or isinstance(timeout_sec, bool):
        raise TypeError("visual comparison timeout_sec must be an integer")
    if timeout_sec <= 0:
        raise ValueError("visual comparison timeout_sec must be positive")


@dataclass(frozen=True, slots=True)
class VisualComparisonToolPolicy:
    """Resolved visual comparison command and execution policy."""

    executable: str
    arguments_template: tuple[str, ...]
    threshold: float
    timeout_sec: int

    def __post_init__(self) -> None:
        _validate_visual_comparison_command(self.executable, self.arguments_template)
        _validate_visual_comparison_placeholders(self.arguments_template)
        _validate_visual_comparison_threshold(self.threshold)
        _validate_visual_comparison_timeout(self.timeout_sec)

    @property
    def argv_template(self) -> tuple[str, ...]:
        """Return the complete command template consumed by the evidence runner."""

        return (self.executable, *self.arguments_template)

    def cache_payload(self) -> dict[str, Any]:
        """Return the stable policy payload included in effective contract identity."""

        return {
            "executable": self.executable,
            "arguments_template": list(self.arguments_template),
            "threshold": self.threshold,
            "timeout_sec": self.timeout_sec,
        }

    def action(
        self,
        action_id: str,
        *,
        reference: str,
        actual: str,
        diff: str,
        regions: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
        required: bool = True,
        depends_on: tuple[str, ...] = (),
    ) -> VerificationAction:
        """Construct a visual action governed by this resolved policy."""

        return visual_diff(
            action_id,
            reference=reference,
            actual=actual,
            diff=diff,
            argv_template=self.argv_template,
            threshold=self.threshold,
            timeout_sec=self.timeout_sec,
            regions=regions,
            required=required,
            depends_on=depends_on,
        )


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
    environment_capabilities: Any
    visual_comparison: VisualComparisonToolPolicy


@dataclass(frozen=True, slots=True)
class OwnerEvidence:
    """Owner-local view over normalized evidence aliases."""

    owner: str
    evidence: VerificationEvidence

    def action(self, local_id: str) -> Any | None:
        """Return one action through the owner's normalized alias."""

        return self.evidence.action(self.owner, local_id)

    def actions(self, *, kind: str | None = None) -> tuple[Any, ...]:
        """Return all actions declared by this owner."""

        prefix = f"{self.owner}."
        canonical_ids = {
            canonical
            for alias, canonical in self.evidence.aliases.items()
            if alias.startswith(prefix)
        }
        return tuple(
            action
            for action_id, action in self.evidence.actions.items()
            if action_id in canonical_ids and (kind is None or action.kind == kind)
        )


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    """Strict raw evidence returned by the bundled evidence runner."""

    actions: dict[str, Any] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    artifacts: tuple[Any, ...] = ()
    metadata: Any | None = None

    @classmethod
    def from_output(cls, output: Any) -> VerificationEvidence:
        """Build indexed evidence from one validated runner output."""

        actions = {action.id: action for action in output.actions}
        unknown_aliases = sorted(set(output.aliases.values()) - set(actions))
        if unknown_aliases:
            raise VerificationEvidenceError(
                "verification evidence aliases reference unknown actions: "
                + ", ".join(unknown_aliases)
            )
        return cls(
            actions=actions,
            aliases=dict(output.aliases),
            artifacts=tuple(output.artifacts),
            metadata=output.metadata,
        )

    def for_owner(self, owner: str) -> OwnerEvidence:
        """Return an owner-local evidence accessor."""

        return OwnerEvidence(owner=owner, evidence=self)

    def action(self, owner: str, local_id: str) -> Any | None:
        """Resolve an owner-local action through the persisted alias map."""

        canonical = self.aliases.get(f"{owner}.{local_id}")
        return self.actions.get(canonical) if canonical else None


@dataclass(frozen=True, slots=True)
class NormalizedVerificationPlan:
    """Namespaced, de-duplicated verification plan for one run."""

    actions: tuple[VerificationAction, ...]
    aliases: dict[str, str]

    def payload(self) -> dict[str, Any]:
        """Return stable JSON-compatible runner input."""

        return {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
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
    timeout_sec: int | None = DEFAULT_SCORER_COMMAND_TIMEOUT_SEC,
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


def scenario_command_plan(owner: str, context: VerificationPlanContext) -> VerificationPlan:
    """Declare scenario-owned verification commands for one scorer."""

    verification = context.scenario.verification
    timeout_sec = verification.preflight_command_timeout_sec or DEFAULT_SCORER_COMMAND_TIMEOUT_SEC
    commands = [(f"gate.{gate.name}", list(gate.command)) for gate in verification.gates]
    commands.extend(
        (f"required.{index}", list(argv))
        for index, argv in enumerate(verification.required_commands)
    )
    deduplicated: list[VerificationAction] = []
    seen: set[tuple[str, ...]] = set()
    for action_id, argv in commands:
        key = tuple(argv)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(command(action_id, argv, timeout_sec=timeout_sec))
    return VerificationPlan(owner=owner, actions=tuple(deduplicated))


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


def structural_snapshot(
    action_id: str,
    paths: list[str] | tuple[str, ...],
    *,
    required: bool = True,
    depends_on: tuple[str, ...] = (),
) -> VerificationAction:
    """Construct a deterministic source-structure snapshot action."""

    return VerificationAction(
        id=action_id,
        kind="structural_snapshot",
        required=required,
        depends_on=depends_on,
        config={"paths": list(paths)},
    )


def public_contract_snapshot(
    action_id: str,
    paths: list[str] | tuple[str, ...],
    *,
    required: bool = True,
    depends_on: tuple[str, ...] = (),
) -> VerificationAction:
    """Construct a deterministic public-contract snapshot action."""

    return VerificationAction(
        id=action_id,
        kind="public_contract_snapshot",
        required=required,
        depends_on=depends_on,
        config={"paths": list(paths)},
    )


def visual_diff(
    action_id: str,
    *,
    reference: str,
    actual: str,
    diff: str,
    argv_template: list[str] | tuple[str, ...],
    threshold: float,
    timeout_sec: int,
    regions: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    required: bool = True,
    depends_on: tuple[str, ...] = (),
) -> VerificationAction:
    """Construct a visual-diff raw evidence action."""

    return VerificationAction(
        id=action_id,
        kind="visual_diff",
        timeout_sec=timeout_sec,
        required=required,
        depends_on=depends_on,
        config={
            "reference": reference,
            "actual": actual,
            "diff": diff,
            "argv_template": list(argv_template),
            "threshold": threshold,
            "regions": list(regions),
        },
    )


def archive(
    action_id: str,
    path: str,
    *,
    output: str | None = None,
    required: bool = True,
    depends_on: tuple[str, ...] = (),
) -> VerificationAction:
    """Construct an archive/copy artifact action."""

    return VerificationAction(
        id=action_id,
        kind="archive",
        required=required,
        depends_on=depends_on,
        config={"path": path, "output": output or f"{action_id}.tar.gz"},
    )


def normalize_verification_plans(
    plans: tuple[VerificationPlan, ...],
    dependencies: dict[str, tuple[EvidenceDependency, ...]] | None = None,
) -> NormalizedVerificationPlan:
    """Namespace, validate, de-duplicate, and topologically order scorer plans."""

    namespaced = _namespace_verification_plans(plans)
    _validate_evidence_dependencies(plans, dependencies or {})
    canonical_actions, aliases = _deduplicate_namespaced_actions(namespaced)
    return NormalizedVerificationPlan(
        actions=_topological_order(canonical_actions),
        aliases=aliases,
    )


def _namespace_verification_plans(
    plans: tuple[VerificationPlan, ...],
) -> tuple[tuple[str, VerificationAction], ...]:
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
    return tuple(namespaced)


def _deduplicate_namespaced_actions(
    namespaced: tuple[tuple[str, VerificationAction], ...],
) -> tuple[tuple[VerificationAction, ...], dict[str, str]]:
    aliases: dict[str, str] = {}
    actions_by_key: dict[str, VerificationAction] = {}
    canonical_by_id: dict[str, VerificationAction] = {}
    namespaced_by_id = {action.id: owner_local for owner_local, action in namespaced}
    for action in _topological_order(tuple(action for _, action in namespaced)):
        owner_local = namespaced_by_id[action.id]
        candidate = VerificationAction(
            id=action.id,
            kind=action.kind,
            timeout_sec=action.timeout_sec,
            required=action.required,
            depends_on=tuple(aliases[dependency] for dependency in action.depends_on),
            config=dict(action.config),
        )
        key = _dedupe_key(candidate)
        canonical = actions_by_key.get(key)
        if canonical is None:
            actions_by_key[key] = candidate
            canonical_by_id[candidate.id] = candidate
            aliases[owner_local] = candidate.id
        else:
            aliases[owner_local] = canonical.id
    return tuple(canonical_by_id.values()), aliases


def _validate_evidence_dependencies(
    plans: tuple[VerificationPlan, ...],
    dependencies: dict[str, tuple[EvidenceDependency, ...]],
) -> None:
    actions_by_owner = {plan.owner: plan.actions for plan in plans}
    missing: list[str] = []
    for owner, required_dependencies in dependencies.items():
        owner_actions = actions_by_owner.get(owner, ())
        for dependency in required_dependencies:
            available = sum(1 for action in owner_actions if action.kind == dependency.kind)
            if available < dependency.minimum:
                missing.append(
                    f"{owner}:{dependency.kind} requires {dependency.minimum}, found {available}"
                )
    if missing:
        raise VerificationPlanError(
            "unsatisfied scorer evidence dependencies: " + "; ".join(missing)
        )


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
    required, optional = {
        "archive": ({"path", "output"}, set()),
        "command": ({"argv"}, {"cwd"}),
        "glob": ({"pattern"}, set()),
        "read_file": ({"path"}, {"max_bytes"}),
        "read_json": ({"path"}, {"max_bytes"}),
        "public_contract_snapshot": ({"paths"}, set()),
        "structural_snapshot": ({"paths"}, set()),
        "visual_diff": (
            {"reference", "actual", "diff", "argv_template", "threshold", "regions"},
            set(),
        ),
    }[action.kind]
    actual = set(action.config)
    if not required.issubset(actual) or not actual.issubset(required | optional):
        raise VerificationPlanError(
            f"verification action {action.id} config keys mismatch: "
            f"required={sorted(required)}, actual={sorted(actual)}"
        )
    validators = {
        "archive": _validate_archive_config,
        "command": _validate_command_config,
        "glob": _validate_glob_config,
        "read_file": _validate_read_config,
        "read_json": _validate_read_config,
        "public_contract_snapshot": _validate_snapshot_config,
        "structural_snapshot": _validate_snapshot_config,
        "visual_diff": _validate_visual_config,
    }
    validators[action.kind](action)


def _validate_archive_config(action: VerificationAction) -> None:
    _validate_config_path(action, "path")
    _validate_config_path(action, "output")


def _validate_glob_config(action: VerificationAction) -> None:
    _validate_config_path(action, "pattern")


def _validate_read_config(action: VerificationAction) -> None:
    _validate_config_path(action, "path")
    max_bytes = action.config.get("max_bytes")
    if max_bytes is not None and (
        not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0
    ):
        raise VerificationPlanError(
            f"verification action {action.id} max_bytes must be a positive integer"
        )


def _validate_snapshot_config(action: VerificationAction) -> None:
    paths = action.config.get("paths")
    if not isinstance(paths, list) or not paths:
        raise VerificationPlanError(
            f"verification action {action.id} snapshot paths must be a non-empty list"
        )
    if len(paths) != len(set(paths)):
        raise VerificationPlanError(
            f"verification action {action.id} snapshot paths must be unique"
        )
    for index, path in enumerate(paths):
        if not isinstance(path, str):
            raise VerificationPlanError(
                f"verification action {action.id} snapshot paths[{index}] is required"
            )
        try:
            validate_relative_path(
                path,
                field_name=f"verification action {action.id}.config.paths[{index}]",
                root_name="workspace",
            )
        except ValueError as exc:
            raise VerificationPlanError(str(exc)) from exc


def _validate_command_config(action: VerificationAction) -> None:
    if action.timeout_sec is None:
        raise VerificationPlanError(
            f"verification action {action.id} command timeout_sec is required"
        )
    argv = action.config.get("argv")
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise VerificationPlanError(f"verification action {action.id} command argv is required")
    try:
        _validate_argv_command(argv, field_name=f"verification action {action.id}.argv")
    except ValueError as exc:
        raise VerificationPlanError(str(exc)) from exc


def _validate_visual_config(action: VerificationAction) -> None:
    for key in ("reference", "actual", "diff"):
        _validate_config_path(action, key)
    _validate_visual_argv_template(action)
    _validate_visual_threshold(action)
    if action.timeout_sec is None:
        raise VerificationPlanError(
            f"verification action {action.id} visual timeout_sec is required"
        )
    _validate_visual_regions(action)


def _validate_visual_argv_template(action: VerificationAction) -> None:
    argv_template = action.config.get("argv_template")
    if not isinstance(argv_template, list) or not all(
        isinstance(item, str) for item in argv_template
    ):
        raise VerificationPlanError(
            f"verification action {action.id} visual argv_template is required"
        )
    required_placeholders = {"{reference}", "{actual}", "{diff}", "{threshold}"}
    if not required_placeholders.issubset(argv_template):
        raise VerificationPlanError(
            f"verification action {action.id} visual argv_template must contain "
            "reference, actual, diff, and threshold placeholders"
        )


def _validate_visual_threshold(action: VerificationAction) -> None:
    threshold = action.config.get("threshold")
    if not isinstance(threshold, int | float) or isinstance(threshold, bool):
        raise VerificationPlanError(f"verification action {action.id} visual threshold is required")
    if not 0 <= float(threshold) <= 1:
        raise VerificationPlanError(
            f"verification action {action.id} visual threshold must be between zero and one"
        )


def _validate_visual_regions(action: VerificationAction) -> None:
    regions = action.config.get("regions")
    if not isinstance(regions, list):
        raise VerificationPlanError(
            f"verification action {action.id} visual regions must be a list"
        )
    for region in regions:
        _validate_visual_region(action, region)


def _validate_visual_region(action: VerificationAction, region: Any) -> None:
    expected = {"name", "reference_image", "actual_image", "diff_image", "weight"}
    if not isinstance(region, dict) or set(region) != expected:
        raise VerificationPlanError(
            f"verification action {action.id} visual region must define "
            + ", ".join(sorted(expected))
        )
    if not isinstance(region["name"], str) or not region["name"]:
        raise VerificationPlanError(
            f"verification action {action.id} visual region name is required"
        )
    weight = region["weight"]
    if not isinstance(weight, int | float) or isinstance(weight, bool) or weight <= 0:
        raise VerificationPlanError(
            f"verification action {action.id} visual region weight must be positive"
        )
    for key in ("reference_image", "actual_image", "diff_image"):
        _validate_visual_region_path(action, region, key)


def _validate_visual_region_path(
    action: VerificationAction, region: dict[str, Any], key: str
) -> None:
    value = region[key]
    if not isinstance(value, str):
        raise VerificationPlanError(
            f"verification action {action.id} visual region {key} is required"
        )
    try:
        validate_relative_path(
            value,
            field_name=f"verification action {action.id}.config.regions[].{key}",
            root_name="workspace",
        )
    except ValueError as exc:
        raise VerificationPlanError(str(exc)) from exc


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
