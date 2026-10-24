"""Host-only verifier bundle: hidden tests plus how each step runs them.

A bundle is a directory that never enters an agent environment::

    <bundle>/oracle-bundle.json   manifest (BundleManifest)
    <bundle>/tests/               hidden test corpus (conftest, helpers, test files)

Step ``pytest_args`` keep the exact order of the source runner's pytest
invocation, with two placeholders rendered per sandbox layout: ``{tests}``
(the copied-in corpus) and ``{app}`` (the copied-in submission).

``build_bundle_from_scb_harbor_task`` derives a bundle from a task produced by
the SCB Harbor converter (``scb-problems/scripts/scb_to_harbor.py``), reusing
its validated per-step pytest selection (oracle-validation.md §2-§3).
"""

from __future__ import annotations

import hashlib
import re
import shlex
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from raidar.contract.snapshot import tree_digest
from raidar.grading.models import VerifierModel

BUNDLE_MANIFEST = "oracle-bundle.json"  # wire name in the problem store (v1 layout)
TESTS_DIR = "tests"
TESTS_PLACEHOLDER = "{tests}"
APP_PLACEHOLDER = "{app}"
# Grader tools Raidar pins itself; converter `--with` pins for these are dropped.
RUNNER_TOOL_NAMES = frozenset(
    {"pytest", "pytest-json-ctrf", "pytest-json-report", "pytest-timeout"}
)
# The converter's reward shim is replaced by Raidar's normalisation.
SCB_EXCLUDED_TEST_FILES = frozenset({"_ctrf_to_reward.py"})
_DROPPED_ARG_PREFIXES = ("--basetemp", "--ctrf", "--json-report")
_DROPPED_ARGS = frozenset({"-rA"})


class BundleError(ValueError):
    """The bundle, or the source it is built from, is malformed."""


class VerifierStepSpec(VerifierModel):
    name: str = Field(min_length=1)
    checkpoint: str = Field(min_length=1)
    test_files: tuple[str, ...] = Field(min_length=1)
    pytest_args: tuple[str, ...]
    timeout_s: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _args_select_test_files(self) -> VerifierStepSpec:
        selected = tuple(
            arg.removeprefix(f"{TESTS_PLACEHOLDER}/")
            for arg in self.pytest_args
            if arg.startswith(f"{TESTS_PLACEHOLDER}/")
        )
        if selected != self.test_files:
            raise ValueError(f"pytest_args select {selected}, test_files say {self.test_files}")
        return self


class GraderSpec(VerifierModel):
    """The grading environment.

    ``environment_dockerfile`` is the agent environment's own Dockerfile, so
    the grader runs the submission with the same system packages and ``ENV``
    as the agent had (git, ps, compilers, ...). A bundle without one declares
    that its agent environment is the bare ``base_image``.
    """

    base_image: str = Field(min_length=1)
    environment_dockerfile: str | None = None
    test_dependencies: tuple[str, ...] = ()
    cpus: float = Field(default=2.0, gt=0)
    memory_mb: int = Field(default=4096, gt=0)


class BundleManifest(VerifierModel):
    schema_version: Literal[1] = 1
    problem: str = Field(min_length=1)
    source: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    grader: GraderSpec
    steps: tuple[VerifierStepSpec, ...] = Field(min_length=1)
    # The problem's snapshot-relative entrypoint (SCB config ``entry_file`` in
    # the pack's format, e.g. ``forge.py``); scorers take it as a config default.
    entry_file: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _unique_steps(self) -> BundleManifest:
        names = [step.name for step in self.steps]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate step names: {names}")
        return self


@dataclass(frozen=True)
class VerifierBundle:
    root: Path
    manifest: BundleManifest

    @property
    def tests_dir(self) -> Path:
        return self.root / TESTS_DIR

    def step(self, name: str) -> VerifierStepSpec:
        for step in self.manifest.steps:
            if step.name == name:
                return step
        raise BundleError(f"bundle {self.manifest.problem!r} has no step {name!r}")

    def resolve_step(self, step_name: str | None) -> str:
        """The step being graded: Harbor's step name, or the only step of a one-step bundle."""
        if step_name is not None:
            return step_name
        if len(self.manifest.steps) != 1:
            raise BundleError("single-step verification needs a one-step bundle")
        return self.manifest.steps[0].name

    def digest(self) -> str:
        return tree_digest(self.root)

    def hidden_file_hashes(self) -> dict[str, str]:
        """sha256 -> relative path of every non-trivial hidden test file."""
        hashes: dict[str, str] = {}
        for path in sorted(self.tests_dir.rglob("*")):
            if not path.is_file() or not path.read_bytes().strip():
                continue
            relative = path.relative_to(self.tests_dir).as_posix()
            hashes.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(), relative)
        return hashes


def load_bundle(root: Path) -> VerifierBundle:
    manifest_path = root / BUNDLE_MANIFEST
    if not manifest_path.is_file():
        raise BundleError(f"no {BUNDLE_MANIFEST} under {root}")
    try:
        manifest = BundleManifest.model_validate_json(manifest_path.read_text())
    except ValidationError as exc:
        raise BundleError(f"invalid {manifest_path}: {exc}") from exc
    bundle = VerifierBundle(root=root.resolve(), manifest=manifest)
    missing = [
        f"{step.name}:{name}"
        for step in manifest.steps
        for name in step.test_files
        if not (bundle.tests_dir / name).is_file()
    ]
    if missing:
        raise BundleError(f"bundle test files missing: {missing}")
    return bundle


def write_bundle(root: Path, manifest: BundleManifest, tests_source: Path) -> VerifierBundle:
    """Write a bundle directory (replacing any previous one) and load it back."""
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(
        tests_source,
        root / TESTS_DIR,
        ignore=lambda _d, names: {n for n in names if n in SCB_EXCLUDED_TEST_FILES},
    )
    # A bundle without an entrypoint keeps its pre-``entry_file`` bytes and digest.
    omit = {"entry_file"} if manifest.entry_file is None else None
    (root / BUNDLE_MANIFEST).write_text(manifest.model_dump_json(indent=2, exclude=omit) + "\n")
    return load_bundle(root)


# ---------------------------------------------------------------- SCB import
_PYTEST_BLOCK = re.compile(r"-m pytest \\\n(?P<args>.*?)\nPYTEST_RC=\$\?", re.S)
_WITH = re.compile(r"--with (\S+)")
_INCLUDE_PRIOR = re.compile(r"^INCLUDE_PRIOR=(true|false)$", re.M)
_PRIOR_ASSIGN = re.compile(
    r'if \[ "\$INCLUDE_PRIOR" = "true" \]; then\n\s*PRIOR_TESTS=\((?P<files>[^)]*)\)'
)
_APP_PATH = re.compile(r"(?<![\w./-])/app(?![\w.-])")
_FROM = re.compile(r"^FROM\s+(\S+)", re.M)
_CONTEXT_INSTRUCTION = re.compile(r"^\s*(COPY|ADD)\s", re.M | re.I)
_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9._-]+")


def _prior_tests(test_sh: str) -> list[str]:
    include = _INCLUDE_PRIOR.search(test_sh)
    assign = _PRIOR_ASSIGN.search(test_sh)
    if include is None or assign is None:
        raise BundleError("test.sh has no INCLUDE_PRIOR/PRIOR_TESTS block")
    return shlex.split(assign.group("files")) if include.group(1) == "true" else []


def _placeholder(token: str) -> str:
    if token.startswith("/tests/"):
        return TESTS_PLACEHOLDER + token.removeprefix("/tests")
    return _APP_PATH.sub(APP_PLACEHOLDER, token)


def _step_args(test_sh: str) -> list[str]:
    block = _PYTEST_BLOCK.search(test_sh)
    if block is None:
        raise BundleError("test.sh has no `-m pytest` invocation")
    tokens = shlex.split(block.group("args").replace("\\\n", " "))
    args: list[str] = []
    for token in tokens:
        if token == "${PRIOR_TESTS[@]}":
            args.extend(_prior_tests(test_sh))
        elif token not in _DROPPED_ARGS and not token.startswith(_DROPPED_ARG_PREFIXES):
            args.append(token)
    return [_placeholder(token) for token in args]


def _timeout(args: list[str]) -> float | None:
    for arg in args:
        if arg.startswith("--timeout="):
            return float(arg.split("=", 1)[1])
    return None


def _checkpoint(args: list[str], default: str) -> str:
    if "--checkpoint" in args:
        index = args.index("--checkpoint")
        if index + 1 < len(args):
            return args[index + 1]
    return default


def _test_dependencies(test_shs: list[str]) -> tuple[str, ...]:
    deps: set[str] = set()
    for text in test_shs:
        for requirement in _WITH.findall(text):
            name = _REQUIREMENT_NAME.match(requirement)
            if name is None or name.group(0).lower() not in RUNNER_TOOL_NAMES:
                deps.add(requirement)
    return tuple(sorted(deps))


def _scb_step(task_dir: Path, name: str) -> tuple[VerifierStepSpec, str]:
    test_sh_path = task_dir / "steps" / name / "tests" / "test.sh"
    if not test_sh_path.is_file():
        raise BundleError(f"missing {test_sh_path}")
    text = test_sh_path.read_text()
    args = _step_args(text)
    spec = VerifierStepSpec(
        name=name,
        checkpoint=_checkpoint(args, name),
        test_files=tuple(
            arg.removeprefix(f"{TESTS_PLACEHOLDER}/")
            for arg in args
            if arg.startswith(f"{TESTS_PLACEHOLDER}/")
        ),
        pytest_args=tuple(args),
        timeout_s=_timeout(args),
    )
    return spec, text


def _scb_grader(task_dir: Path, test_shs: list[str]) -> GraderSpec:
    dockerfile = (task_dir / "environment" / "Dockerfile").read_text()
    base = _FROM.search(dockerfile)
    if base is None:
        raise BundleError(f"{task_dir}/environment/Dockerfile has no FROM")
    if _CONTEXT_INSTRUCTION.search(dockerfile):
        # The grader image is built without the task's build context.
        raise BundleError(f"{task_dir}/environment/Dockerfile uses COPY/ADD; not supported")
    return GraderSpec(
        base_image=base.group(1),
        environment_dockerfile=dockerfile,
        test_dependencies=_test_dependencies(test_shs),
    )


def build_bundle_from_scb_harbor_task(
    task_dir: Path, out_dir: Path, *, source: dict[str, str] | None = None
) -> VerifierBundle:
    """Derive a host-only bundle from an SCB-converted Harbor multi-step task."""
    task = tomllib.loads((task_dir / "task.toml").read_text())
    step_names = [str(step["name"]) for step in task.get("steps", [])]
    if not step_names:
        raise BundleError(f"{task_dir} declares no [[steps]]")
    built = [_scb_step(task_dir, name) for name in step_names]
    manifest = BundleManifest(
        problem=str(task.get("metadata", {}).get("source_problem") or task_dir.name),
        source={
            "format": "scb-harbor-task",
            "task_digest": tree_digest(task_dir),
            **(source or {}),
        },
        env={str(k): str(v) for k, v in task.get("environment", {}).get("env", {}).items()},
        grader=_scb_grader(task_dir, [text for _, text in built]),
        steps=tuple(spec for spec, _ in built),
    )
    return write_bundle(out_dir, manifest, task_dir / "tests")
