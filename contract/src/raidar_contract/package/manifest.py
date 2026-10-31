"""``stage/1``: the strict, pure-data manifest of a stage package and the loaded package.

Unknown keys are refused at every level. Nothing here touches the filesystem or imports a
primitive; the checks that need the tree live in ``registry``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from raidar_contract.package import StagePackageError
from raidar_contract.package.digest import FIXTURES_DIR, StageFile

STAGE_CONTRACT = "stage/1"
STATE_ROOT = "/app/.raidar"
MAX_TIMEOUT_S = 300
MAX_STOP_ROUNDS = 5

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PARAM_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_COMMAND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CONTRACT_RE = re.compile(r"^[a-z][a-z0-9-]*/[1-9][0-9]*$")
_INPUT_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.[A-Za-z0-9_-]+$")


ParamScalar = str | int | float | bool | None


def _finite_scalar(value: ParamScalar) -> ParamScalar:
    if isinstance(value, float) and not math.isfinite(value):
        raise StagePackageError("params must be finite JSON scalars")
    return value


ParamValue = Annotated[
    StrictBool | StrictInt | StrictFloat | StrictStr | None,
    AfterValidator(_finite_scalar),
]


def _argv(value: tuple[str, ...]) -> tuple[str, ...]:
    if not value or not all(value):
        raise ValueError("an argv needs at least one nonempty element")
    return value


def _state_path(value: str) -> str:
    path = PurePosixPath(value)
    clean = path.is_absolute() and ".." not in path.parts and str(path) == value
    if not clean or not value.startswith(f"{STATE_ROOT}/"):
        raise ValueError(f"{value!r}: must be a normalised path below {STATE_ROOT}/")
    return value


def _matching(pattern: re.Pattern[str], what: str) -> Callable[[str], str]:
    def check(value: str) -> str:
        if not pattern.fullmatch(value):
            raise ValueError(f"invalid {what} {value!r}")
        return value

    return check


Argv = Annotated[tuple[StrictStr, ...], AfterValidator(_argv)]
StatePath = Annotated[StrictStr, AfterValidator(_state_path)]
Timeout = Annotated[StrictInt | StrictFloat, Field(gt=0, le=MAX_TIMEOUT_S)]
Leaf = Annotated[StrictStr, AfterValidator(_matching(NAME_RE, "name"))]
Contract = Annotated[StrictStr, AfterValidator(_matching(_CONTRACT_RE, "contract id"))]
OutputContract = Literal["criteria/1"]  # criteria.CRITERIA_CONTRACT, the one structured contract


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StageInput(_Strict):
    """An earlier package's output this package consumes."""

    from_: Annotated[
        StrictStr, AfterValidator(_matching(_INPUT_RE, "producer")), Field(alias="from")
    ]
    contract: OutputContract


class StageTool(_Strict):
    project: Literal["tool"] = "tool"
    commands: dict[
        Annotated[StrictStr, AfterValidator(_matching(_COMMAND_RE, "command"))], Argv
    ] = Field(default_factory=dict)


class Call(_Strict):
    run: Argv
    timeout_s: Timeout = 60


class GateCheck(_Strict):
    name: Annotated[StrictStr, Field(min_length=1)]
    run: Argv | None = None
    kind: Literal["command", "pytest"] = "command"
    before: StrictBool = False
    watch: StatePath | None = None
    timeout_s: Timeout = 60

    @model_validator(mode="after")
    def _command_runs(self) -> GateCheck:
        if self.kind == "command" and self.run is None:
            raise ValueError(f"check {self.name!r} is a command and needs run")
        return self


class Stop(_Strict):
    run: Argv
    watch: StatePath | None = None
    timeout_s: Timeout = 60
    max_rounds: Annotated[StrictInt, Field(ge=0, le=MAX_STOP_ROUNDS)] = 0


class Output(_Strict):
    path: StatePath
    contract: OutputContract | None = None


def _token_of(param: str) -> str:
    return param.replace("-", "_").upper()


class StageManifest(_Strict):
    contract: Literal["stage/1"]
    name: Leaf
    summary: Annotated[StrictStr, Field(min_length=1)]
    requires: dict[StrictStr, Contract] = Field(default_factory=dict)
    params: dict[
        Annotated[StrictStr, AfterValidator(_matching(_PARAM_RE, "param"))], ParamValue
    ] = Field(default_factory=dict)
    tokens: tuple[Annotated[StrictStr, AfterValidator(_matching(_TOKEN_RE, "token"))], ...] = ()
    inputs: dict[Leaf, StageInput] = Field(default_factory=dict)
    tool: StageTool | None = None
    setup: Call | None = None
    gate: tuple[GateCheck, ...] = ()
    on_stop: Stop | None = None
    outputs: dict[Leaf, Output] = Field(default_factory=dict)
    skills: tuple[Leaf, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> StageManifest:
        needs = {
            "setup/1": self.setup,
            "gate/1": self.gate,
            "stop/1": self.on_stop,
            "outputs/1": self.outputs,
        }
        for contract, used in needs.items():
            if used and contract not in self.requires.values():
                raise ValueError(f"declares a trigger of {contract} without requiring it")
        for label, values in (
            ("tokens", self.tokens),
            ("gate check names", [check.name for check in self.gate]),
            ("skills", self.skills),
            ("param tokens", [_token_of(name) for name in self.params]),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"duplicate {label}")
        if set(self.tokens) & set(self.param_tokens()):
            raise ValueError("tokens collide with params")
        return self

    def param_tokens(self) -> dict[str, str]:
        """Frame token name (``%%%NAME%%%`` inner text) of every declared param."""
        return {_token_of(name): name for name in self.params}

    def token_names(self) -> tuple[str, ...]:
        """The tokens setup/stop return, in declaration order."""
        return self.tokens

    def effective_params(
        self, overrides: Mapping[str, ParamScalar] | None = None
    ) -> dict[str, ParamScalar]:
        """The defaults with ``overrides`` laid over; keys must be declared, JSON types kept."""
        effective: dict[str, ParamScalar] = dict(self.params)
        for key, value in (overrides or {}).items():
            if key not in self.params:
                raise StagePackageError(f"{self.name}: param {key!r} is not declared")
            if type(value) is not type(self.params[key]):
                raise StagePackageError(
                    f"{self.name}: param {key!r} must stay "
                    f"{type(self.params[key]).__name__}, not {type(value).__name__}"
                )
            effective[key] = _finite_scalar(value)
        return effective


def parse_manifest(text: str) -> StageManifest:
    """The manifest of ``stage.yaml`` text; ``StagePackageError`` names what is refused."""
    try:
        data = yaml.safe_load(text)
        return StageManifest.model_validate(data)
    except (yaml.YAMLError, ValidationError) as error:
        raise StagePackageError(f"invalid stage manifest: {error}") from error


@dataclass(frozen=True)
class StagePackage:
    """A loaded package: validated manifest, prompt, optional instructions and file inventory."""

    root: Path
    manifest: StageManifest
    digest: str
    prompt: str
    instructions: str | None
    files: tuple[StageFile, ...]

    @property
    def name(self) -> str:
        return self.manifest.name

    def effective_params(
        self, overrides: Mapping[str, ParamScalar] | None = None
    ) -> dict[str, ParamScalar]:
        return self.manifest.effective_params(overrides)

    def fixture_cases(self) -> tuple[str, ...]:
        folder = self.root / FIXTURES_DIR
        if not folder.is_dir():
            return ()
        return tuple(sorted(item.name for item in folder.iterdir() if item.is_dir()))

    def fixture_dir(self, case: str) -> Path:
        if case not in self.fixture_cases():
            raise StagePackageError(f"{self.name}: no fixture {case!r}")
        return self.root / FIXTURES_DIR / case
