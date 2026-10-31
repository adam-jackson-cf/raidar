"""Workflow definitions: ``lab/workflows/<name>/workflow.yaml``, released as ``<name>@N``.

A workflow names the stages a checkpoint runs through. There are three stage types
(``define``, ``build``, ``verify``), each with one implementation in Raidar; a workflow holds
its own settings for each stage it uses.

``define`` (design section 5.1) is the builder's own first work in the checkpoint's one session:
it builds a feature definition with the ``define`` tool (the stage's payload), seals it, and the
build then works from the file the stage writes. ``define.prompt`` carries ``%%%UNITS%%%``
(exactly once, the checkpoint's numbered spec) and no other token; ``unit_rules`` is the unit
rules version, ``max_refinement_rounds`` bounds the question rounds, and ``aspects``,
``anchored`` and ``audited`` are the tool's checks (all on).

``build.prompt`` frames the checkpoint's one prompt: ``%%%TASK%%%`` (exactly once) is replaced
by the checkpoint's task text; when ``define`` is enabled ``%%%DEFINE%%%`` (exactly once) by the
define stage's rendered prompt and, when ``verify`` is enabled, ``%%%VERIFY%%%`` (exactly once)
by the verify stage's prompt; a workflow may carry neither token for a stage it does not run.

``verify`` (design section 5.2, spike-sized) gates the work through a pre-commit hook and the
runner's own commit after every turn: ``checks`` are the gate's checks (one ``tests`` check,
which is pytest with at least one test and none failing; the others run their pinned tool's
command over the workspace's Python files), ``max_fix_rounds`` bounds the fix turns a refusal
starts, ``test_timeout`` stops a single test, and ``rules_file`` is where the gate's rules are
written for the agent. Its ``prompt`` may use the tokens ``%%%MAX_FIX_ROUNDS%%%``,
``%%%RULES_FILE%%%`` and ``%%%TEST_TIMEOUT_SECONDS%%%`` and no other.

With ``verify.judge`` (and ``verify.rework``, the pushback prompt with ``%%%PUSHBACK%%%`` and
``%%%GATE%%%`` once each) the engine judges each accepted seal's criteria host-side and sends the
open ones back to the builder, up to ``verify.max_passes`` passes; it needs the define stage (the
criteria) and ``build.structured`` (each build turn ends with a JSON reply
``{account, ac_tests, adrs}``, the shape appended by the runner). Without a judge the one pass is
the gate's.

A workflow directory holds ``workflow.yaml`` and the skills its stages place
(``skills/<name>/...``, listed in a stage's ``skills``). Every file under ``skills/`` is part of
the definition as ``components`` (path -> text) and so of its content hash; a file no stage's
``skills`` accounts for, or a listed skill without its ``SKILL.md``, is refused. The content hash
covers the resolved definition (every default filled, empty ``skills`` and ``components`` left
out, a stage the workflow does not name absent), so editing a file needs a new release in
``lab/releases.yaml``.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from raidar_contract.canonical import IGNORED_TREE_NAMES, digest_json

WORKFLOW_FILE = "workflow.yaml"
SKILLS_DIR = "skills"
SKILL_FILE = "SKILL.md"
StageType = Literal["define", "build", "verify"]
STAGE_TYPES: tuple[StageType, ...] = ("define", "build", "verify")
IMPLEMENTED_STAGES: tuple[StageType, ...] = STAGE_TYPES
TASK_TOKEN = "%%%TASK%%%"
DEFINE_TOKEN = "%%%DEFINE%%%"
UNITS_TOKEN = "%%%UNITS%%%"
VERIFY_TOKEN = "%%%VERIFY%%%"
VERIFY_PROMPT_TOKENS = frozenset({"MAX_FIX_ROUNDS", "RULES_FILE", "TEST_TIMEOUT_SECONDS"})
REWORK_TOKENS = frozenset({"PUSHBACK", "GATE"})
NAME_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
PIN_PATTERN = r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==[0-9][A-Za-z0-9.+!-]*$"
COMPONENT_PATH = re.compile(r"^skills/[a-z0-9][a-z0-9-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)+$")
TOKEN = re.compile(r"%%%([A-Z_]+)%%%")
CheckCategory = Literal["lint", "format", "types", "tests"]


class WorkflowDefinitionError(ValueError):
    """A workflow definition is missing, malformed or uses a stage this Raidar lacks."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


SkillNames = Annotated[tuple[Annotated[str, Field(pattern=NAME_PATTERN)], ...], Field()]


class DefineSettings(_Model):
    """The define stage: the tool's checks, its rounds, and the prompt that frames the work."""

    enabled: bool
    prompt: str = Field(min_length=1)
    unit_rules: Literal[2]
    max_refinement_rounds: int = Field(ge=0)
    aspects: Literal[True]
    anchored: Literal[True]
    audited: Literal[True]
    skills: SkillNames = ()

    @model_validator(mode="after")
    def _units_token_only(self) -> Self:
        count = self.prompt.count(UNITS_TOKEN)
        if count != 1:
            raise ValueError(
                f"define.prompt must contain {UNITS_TOKEN} exactly once (found {count})"
            )
        unknown = sorted(set(TOKEN.findall(self.prompt)) - {"UNITS"})
        if unknown:
            raise ValueError(f"define.prompt uses unknown token(s) {unknown}")
        return self

    def render(self, units: str) -> str:
        """The define prompt with the checkpoint's numbered units filled in."""
        return self.prompt.replace(UNITS_TOKEN, units)


class BuildSettings(_Model):
    prompt: str = Field(min_length=1)
    skills: SkillNames = ()
    structured: bool = False
    """Each turn ends with a JSON reply ``{account, ac_tests, adrs}`` (shape appended)."""

    @model_validator(mode="after")
    def _one_task_token(self) -> Self:
        count = self.prompt.count(TASK_TOKEN)
        if count != 1:
            raise ValueError(f"build.prompt must contain {TASK_TOKEN} exactly once (found {count})")
        return self


class GateCheck(_Model):
    """One gate check: a pinned tool and, unless it is the tests check, the command it runs."""

    name: str = Field(pattern=NAME_PATTERN)
    category: CheckCategory
    tool: str = Field(pattern=PIN_PATTERN)
    run: str | None = None
    blocking: bool

    @model_validator(mode="after")
    def _command_runs_the_pinned_tool(self) -> Self:
        if self.category == "tests":
            if self.run is not None:
                raise ValueError(f"check {self.name!r}: the tests check is pytest and takes no run")
            if self.package != "pytest":
                raise ValueError(f"check {self.name!r}: the tests check's tool is pytest")
            return self
        argv = shlex.split(self.run or "")
        if not argv or argv[0] != self.package:
            raise ValueError(f"check {self.name!r}: run must start with its tool {self.package!r}")
        return self

    @property
    def package(self) -> str:
        found = re.match(PIN_PATTERN, self.tool)
        assert found is not None  # the field's pattern
        return found["name"]

    @property
    def argv(self) -> list[str]:
        return shlex.split(self.run or "")


class GateTriggers(_Model):
    """When the gate runs: on the agent's own commits (the hook) and after every turn (the seal)."""

    on_commit: Literal[True]
    after_turn: Literal[True]


class JudgeSettings(_Model):
    """The engine's judge of the acceptance criteria (factory-x-loops ``[judge]``), host-side."""

    model: str = Field(min_length=1)
    question_set: Literal["dbv5-aspect/1"]
    closed_at: float = Field(gt=0, le=1)
    open_at: float = Field(ge=0, lt=1)
    regression_needs_baseline: bool
    retries: int = Field(ge=0)
    timeout_s: float = Field(gt=0)
    context_tokens: int = Field(gt=0)
    stall_repeats: int = Field(gt=0)
    fit_rework: bool

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> Self:
        if self.open_at >= self.closed_at:
            raise ValueError("judge.open_at must be below judge.closed_at")
        return self


class VerifySettings(_Model):
    enabled: bool
    language: Literal["python"]
    prompt: str = Field(min_length=1)
    gate: GateTriggers
    max_fix_rounds: int = Field(default=0, ge=0)
    max_passes: int = Field(ge=1)
    test_timeout: int = Field(gt=0)
    rules_file: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    checks: tuple[GateCheck, ...] = Field(min_length=1)
    skills: SkillNames = ()
    judge: JudgeSettings | None = None
    rework: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        unknown = sorted(set(TOKEN.findall(self.prompt)) - VERIFY_PROMPT_TOKENS)
        if unknown:
            raise ValueError(f"verify.prompt uses unknown token(s) {unknown}")
        self._rework_consistent()
        names = [check.name for check in self.checks]
        if len(set(names)) != len(names):
            raise ValueError(f"verify.checks repeats a name: {names}")
        tests = [check for check in self.checks if check.category == "tests"]
        if len(tests) != 1 or not tests[0].blocking:
            raise ValueError("verify.checks needs exactly one blocking tests check")
        return self

    def _rework_consistent(self) -> None:
        """``judge`` and ``rework`` come together; a rework needs a prompt with its two tokens."""
        if (self.judge is None) != (self.rework is None):
            raise ValueError("verify.judge and verify.rework are set together or not at all")
        if self.judge is None and self.max_passes != 1:
            raise ValueError("verify.max_passes above 1 needs verify.judge: passes are judged")
        if self.rework is not None:
            found = sorted(TOKEN.findall(self.rework))
            if found != sorted(REWORK_TOKENS):
                raise ValueError(
                    f"verify.rework must contain {sorted(REWORK_TOKENS)} once each (found {found})"
                )

    def render(self) -> str:
        """The verify prompt with its tokens filled from these settings."""
        values = {
            "MAX_FIX_ROUNDS": str(self.max_fix_rounds),
            "RULES_FILE": self.rules_file,
            "TEST_TIMEOUT_SECONDS": str(self.test_timeout),
        }
        return TOKEN.sub(lambda found: values[found[1]], self.prompt)

    @property
    def pins(self) -> list[str]:
        """Every tool pin the gate installs, once each, sorted."""
        return sorted({check.tool for check in self.checks})


class Stages(_Model):
    define: DefineSettings | None = None
    build: BuildSettings
    verify: VerifySettings | None = None

    @model_validator(mode="before")
    @classmethod
    def _known_stages_only(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for name in data:
                if name not in STAGE_TYPES:
                    raise ValueError(
                        f"unknown stage type {name!r}; stage types are {', '.join(STAGE_TYPES)}"
                    )
        return data

    @model_validator(mode="after")
    def _stage_tokens_iff_stage(self) -> Self:
        for token, stage in ((DEFINE_TOKEN, self.define), (VERIFY_TOKEN, self.verify)):
            count = self.build.prompt.count(token)
            expected = 1 if stage is not None and stage.enabled else 0
            if count != expected:
                raise ValueError(
                    f"build.prompt must contain {token} {expected} time(s) "
                    f"(found {count}): exactly once when its stage is enabled, never otherwise"
                )
        return self

    @model_validator(mode="after")
    def _judge_has_its_inputs(self) -> Self:
        """The judge reads criteria from the define stage and ``ac_tests`` from replies."""
        verify = self.verify
        if verify is None or not verify.enabled or verify.judge is None:
            return self
        if self.define is None or not self.define.enabled:
            raise ValueError("verify.judge needs the define stage: its criteria are judged")
        if not self.build.structured:
            raise ValueError(
                "verify.judge needs build.structured: criteria map to tests in replies"
            )
        return self

    @property
    def enabled(self) -> dict[StageType, DefineSettings | BuildSettings | VerifySettings]:
        """The enabled stages' settings by stage type, in stage order."""
        found: dict[StageType, DefineSettings | BuildSettings | VerifySettings] = {}
        if self.define is not None and self.define.enabled:
            found["define"] = self.define
        found["build"] = self.build
        if self.verify is not None and self.verify.enabled:
            found["verify"] = self.verify
        return found


class WorkflowDefinition(_Model):
    schema_: Literal[1] = Field(alias="schema")
    name: str = Field(pattern=NAME_PATTERN)
    stages: Stages
    components: dict[str, str] = Field(default_factory=dict)
    """The workflow's files below its directory (``skills/<name>/...``) by path, as text."""

    @model_validator(mode="after")
    def _components_are_the_listed_skills(self) -> Self:
        stray = sorted(path for path in self.components if not COMPONENT_PATH.match(path))
        if stray:
            raise ValueError(f"workflow files must be skills/<name>/... files, found {stray}")
        listed = self._listed_skills()
        present = {path.split("/")[1] for path in self.components}
        if present != set(listed):
            raise ValueError(
                f"skills listed by stages {sorted(listed)} "
                f"differ from skills held {sorted(present)}"
            )
        missing = sorted(
            skill for skill in listed if f"{SKILLS_DIR}/{skill}/{SKILL_FILE}" not in self.components
        )
        if missing:
            raise ValueError(f"skill(s) {missing} hold no {SKILL_FILE}")
        return self

    def _listed_skills(self) -> list[str]:
        listed = [
            skill
            for settings in (self.stages.define, self.stages.build, self.stages.verify)
            if settings is not None
            for skill in settings.skills
        ]
        if len(set(listed)) != len(listed):
            raise ValueError(f"a skill is listed by more than one stage: {sorted(listed)}")
        return listed

    @property
    def enabled_stages(self) -> tuple[StageType, ...]:
        """The stage types this workflow runs, in stage order."""
        return tuple(self.stages.enabled)

    def to_json(self) -> dict[str, object]:
        """The resolved definition; empty ``skills``, a build that is not ``structured`` and empty
        ``components`` are left out (a workflow that does not use them keeps its hash)."""
        document = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        stages: dict[str, dict[str, object]] = document["stages"]
        for settings in stages.values():
            if not settings.get("skills"):
                settings.pop("skills", None)
        if not stages["build"].get("structured"):
            stages["build"].pop("structured", None)
        if not document["components"]:
            del document["components"]
        return document

    @property
    def digest(self) -> str:
        """Content hash of the resolved definition: what a release registers."""
        return digest_json(self.to_json())


def workflow_dir(root: Path, name: str) -> Path:
    return root / name


def _folder_files(folder: Path, definition_file: Path) -> dict[str, str]:
    """Every file below ``folder`` but its definition, by posix path, as text."""
    files: dict[str, str] = {}
    for item in sorted(folder.rglob("*")):
        relative = item.relative_to(folder).as_posix()
        if IGNORED_TREE_NAMES.intersection(relative.split("/")) or item.is_dir():
            continue
        if item == definition_file:
            continue
        if item.is_symlink() or not item.is_file():
            raise WorkflowDefinitionError(f"{item}: workflow files must be regular files")
        try:
            files[relative] = item.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise WorkflowDefinitionError(f"{item}: unreadable workflow file: {exc}") from exc
    return files


def load_workflow(root: Path, name: str) -> WorkflowDefinition:
    """Load and validate ``<root>/<name>/workflow.yaml`` and its files; ``name`` equals the dir."""
    folder = workflow_dir(root, name)
    path = folder / WORKFLOW_FILE
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(document, dict) and "components" in document:
            raise WorkflowDefinitionError(
                f"{path}: components are the folder's files, not set here"
            )
        definition = WorkflowDefinition.model_validate(
            {**document, "components": _folder_files(folder, path)}
        )
    except (OSError, yaml.YAMLError, TypeError) as exc:
        raise WorkflowDefinitionError(f"{path}: unreadable workflow definition: {exc}") from exc
    except ValidationError as exc:
        raise WorkflowDefinitionError(f"{path}: invalid workflow definition: {exc}") from exc
    if definition.name != name:
        raise WorkflowDefinitionError(f"{path}: name {definition.name!r} must equal its directory")
    return definition


def workflow_names(root: Path) -> list[str]:
    """Every workflow defined under ``root``."""
    return sorted(path.parent.name for path in root.glob(f"*/{WORKFLOW_FILE}"))
