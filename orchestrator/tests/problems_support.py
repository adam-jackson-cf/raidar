"""Builders for problems-store tests: a tiny SCB-shaped two-checkpoint problem.

``build_fixture_store`` writes a complete store (pins, manifests, task, oracle
bundle, references) for two problems:

- ``fixture/adder``: reference 1 implements ``add``, reference 2 adds ``mul``;
  it validates.
- ``fixture/adder_negative``: both references exit 1 on every call, so no step has a
  scoreable case and validation must exclude it.

Tests build the store into a temporary directory; no copy is committed.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from raidar.design.problem.content import content_digest
from raidar.design.problem.models import (
    PackBinding,
    ProblemId,
    ProblemManifest,
    ProblemMode,
    ProblemSource,
    ProblemStep,
    dependency_content_key,
)
from raidar.design.problem.store import ProblemStore
from raidar.design.problem.validation import ValidationReport
from raidar.design.problem.validator import ValidateOptions, validate_problem, write_report
from raidar.grading.bundle import (
    BundleManifest,
    GraderSpec,
    VerifierStepSpec,
    write_bundle,
    write_bundle_dependencies,
)
from raidar.grading.dependency_pins import DependencyLocks, Unprovided
from raidar.grading.records import grading_execution_digest
from raidar.grading.resolver import Resolution, Resolver, resolve_pins
from tests.verifier_support import LocalProcessSandbox, write_app, write_tests

FIXTURE_COMMIT = "fe" * 20
FIXTURE_VERSION = FIXTURE_COMMIT[:12]
FIXTURE_BASE_IMAGE = (
    "ghcr.io/astral-sh/uv:python3.12-trixie-slim"
    "@sha256:4677e08839853fe91c523b593f822ec1e87c7b91ba4c6b30929016b2e0933cd5"
)
POSITIVE_REF = f"fixture/adder@{FIXTURE_VERSION}"
NEGATIVE_REF = f"fixture/adder_negative@{FIXTURE_VERSION}"
ONESHOT_REF = f"fixture/adder.checkpoint_1@{FIXTURE_VERSION}"
SOURCES_YAML = f"""# Test fixture store (orchestrator/tests/problems_support.py builds it).
sources:
  fixture:
    format: scb-problems
    url: fixture://adder
    commit: {FIXTURE_COMMIT}
"""
TASK_TOML = """schema_version = "1.1"

[task]
name = "{name}"

[environment]
docker_image = "{image}"
workdir = "/app"
"""


def _step(name: str, files: list[str]) -> VerifierStepSpec:
    return VerifierStepSpec(
        name=name,
        checkpoint=name,
        test_files=tuple(files),
        pytest_args=(
            *(f"{{tests}}/{f}" for f in files),
            "--checkpoint",
            name,
            "--entrypoint",
            "python3 {app}/app.py",
        ),
        timeout_s=None,
    )


FIXTURE_CUTOFF = "2026-10-01T00:00:00Z"


class FakeResolver:
    """An offline, deterministic resolver: every requirement resolves to version 1.0.

    ``unprovided`` is returned for every set; ``cutoffs`` records the cutoffs it was asked for.
    """

    identity = "fake-uv@0"

    def __init__(self, unprovided: tuple[Unprovided, ...] = ()) -> None:
        self.unprovided = unprovided
        self.cutoffs: list[str] = []

    def __call__(self, requirements: Sequence[str], *, arch: str, exclude_newer: str) -> Resolution:
        self.cutoffs.append(exclude_newer)
        names = sorted({canonicalize_name(Requirement(text).name) for text in requirements})
        packages = "".join(f'\n[[packages]]\nname = "{name}"\nversion = "1.0"\n' for name in names)
        lock = f'lock-version = "1.0"\ncreated-by = "fake"\n{packages}'
        return Resolution(lock, tuple(f"{name}==1.0" for name in names), self.unprovided, 0.0)


def _write_problem(
    store: ProblemStore, name: str, *, broken: bool, oneshot: bool, resolver: Resolver
) -> None:
    ref = f"{name}@{FIXTURE_VERSION}"
    steps = ["checkpoint_1"] if oneshot else ["checkpoint_1", "checkpoint_2"]
    pins, locks = resolve_pins(
        dict.fromkeys(steps, ()), (), exclude_newer=FIXTURE_CUTOFF, resolver=resolver
    )
    content = store.content_dir(ProblemId.parse(ref), dependency_content_key(pins))
    work = content.parent / "work"
    step_specs = {
        "checkpoint_1": _step("checkpoint_1", ["test_checkpoint_1.py"]),
        "checkpoint_2": _step("checkpoint_2", ["test_checkpoint_1.py", "test_checkpoint_2.py"]),
    }
    bundle = write_bundle(
        content / "oracle",
        BundleManifest(
            problem=ref,
            source={"format": "test-fixture"},
            env={"TERM": "xterm"},
            grader=GraderSpec(base_image=FIXTURE_BASE_IMAGE),
            steps=tuple(step_specs[s] for s in steps),
        ),
        write_tests(work / "tests"),
    )
    write_app(content / "references" / "checkpoint_1")
    if not oneshot:
        write_app(content / "references" / "checkpoint_2", mul=True)
    if broken:
        for app in (content / "references").glob("*/app.py"):
            app.write_text("import sys\n\nsys.exit(1)\n")
    bundle = write_bundle_dependencies(bundle.root, pins, locks)
    DependencyLocks(store.deps_dir(ProblemId.parse(ref))).write_all(locks)
    task = content / "task"
    (task / "tests").mkdir(parents=True)
    (task / "tests" / "test.sh").write_text("#!/bin/bash\nexit 1\n")
    (task / "task.toml").write_text(
        TASK_TOML.format(name=name, image=FIXTURE_BASE_IMAGE)
        + "".join(f'\n[[steps]]\nname = "{step}"\n' for step in steps)
    )
    manifest_steps = []
    for step in steps:
        instruction = task / "steps" / step / "instruction.md"
        instruction.parent.mkdir(parents=True)
        instruction.write_text(textwrap.dedent(f"# {step}\nImplement the {step} operations.\n"))
        manifest_steps.append(
            ProblemStep(
                name=step,
                checkpoint=step,
                instruction_sha256=hashlib.sha256(instruction.read_bytes()).hexdigest(),
                test_files=step_specs[step].test_files,
            )
        )
    shutil.rmtree(work)
    store.write_manifest(
        ProblemManifest(
            ref=ref,
            mode=ProblemMode.ONE_SHOT if oneshot else ProblemMode.MULTI_CHECKPOINT,
            checkpoints=len(steps),
            steps=tuple(manifest_steps),
            source=ProblemSource(
                dataset="fixture",
                url="fixture://adder",
                commit=FIXTURE_COMMIT,
                problem=name.split("/", 1)[1].split(".", 1)[0],
                checkpoint_selection="checkpoint_1" if oneshot else None,
                problem_tree="fixture",
                converter_tree="fixture",
                converted_task_sha256="fixture",
            ),
            pack=PackBinding(id="python", version=1, image=FIXTURE_BASE_IMAGE),
            task_sha256=content_digest(task),
            bundle_sha256=content_digest(bundle.root),
            dependencies=pins,
            references_sha256=content_digest(content / "references"),
        )
    )


def build_fixture_store(
    root: Path, data_root: Path | None = None, *, resolver: Resolver | None = None
) -> ProblemStore:
    root.mkdir(parents=True, exist_ok=True)
    (root / "sources.yaml").write_text(SOURCES_YAML)
    store = ProblemStore(root, data_root)
    chosen = resolver or FakeResolver()
    _write_problem(store, "fixture/adder", broken=False, oneshot=False, resolver=chosen)
    _write_problem(store, "fixture/adder_negative", broken=True, oneshot=False, resolver=chosen)
    _write_problem(store, "fixture/adder.checkpoint_1", broken=False, oneshot=True, resolver=chosen)
    return store


FIXTURE_GRADER_ENVIRONMENT: dict[str, object] = {
    "grader_image": "raidar-oracle-grader:fixture",
    "grader_environment_id": "sha256:" + "f" * 64,
    "grader_implementation_sha256": grading_execution_digest(),
}
"""The identity a fixture report records for its local grader (the real one needs Docker)."""


def record_validation(
    store: ProblemStore, ref: str, work: Path, repeats: int = 2
) -> ValidationReport:
    """Really validate a fixture problem (local grader) and write its committed report."""
    work.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(
        validate_problem(
            store,
            ProblemId.parse(ref),
            ValidateOptions(docker_context="test-ctx", repeats=repeats, concurrency=1),
            sandbox=LocalProcessSandbox(work / "sandbox"),
            work_root=work,
        )
    )
    report.environment.update(FIXTURE_GRADER_ENVIRONMENT)
    write_report(report, store.validation_path(ProblemId.parse(ref)))
    return report


# ------------------------------------------------------------ fake SCB dataset
CONVERTED_TASK_TOML = """schema_version = "1.1"
artifacts = []

[task]
name = "scb/adder"
description = "Adder CLI."

[metadata]
source = "scb-problems"
source_problem = "adder"

[environment]
build_timeout_sec = 600.0
cpus = 1
memory_mb = 2048
workdir = "/app"

[environment.env]
TERM = "xterm"

[agent]
timeout_sec = 7200.0

[verifier]
timeout_sec = 900.0

[[steps]]
name = "checkpoint_1"
min_reward = { core_pass_rate = 0.0, strict_pass_rate = 0.0 }

[[steps]]
name = "checkpoint_2"
min_reward = { core_pass_rate = 0.0, strict_pass_rate = 0.0 }
"""
CONVERTED_TEST_SH = """#!/bin/bash
set +e
cleanup_verifier_state() {{
  find /tests -mindepth 1 -delete
}}
trap cleanup_verifier_state EXIT

INCLUDE_PRIOR={include_prior}
PRIOR_TESTS=()
if [ "$INCLUDE_PRIOR" = "true" ]; then
  PRIOR_TESTS=({prior})
fi

"${{PYTEST_USER_PREFIX[@]}}" uv run \\
  --with pytest==9.0.3 \\
  --with pyyaml \\
  -m pytest \\
    --timeout=30.0 \\
    --basetemp="$PYTEST_TMP_ROOT" \\
    "${{PRIOR_TESTS[@]}}" \\
    /tests/test_{step}.py \\
    --checkpoint "{step}" \\
    --entrypoint "uv run --project /app app.py" \\
    --ctrf=/tmp/ctrf.json \\
    -rA
PYTEST_RC=$?
python3 /tests/_ctrf_to_reward.py --out-dir /logs/verifier
"""
FAKE_CONVERTER_CLI = """
import shutil
import sys
import time
from pathlib import Path


def resolve_harbor_command(repo_root):
    raise AssertionError("the importer must not probe the Harbor CLI")


def main(argv):
    out = Path(argv[argv.index("--out") + 1])
    problem = argv[-1]
    repo = Path(__file__).resolve().parents[2]
    target = out / "scb" / problem
    shutil.copytree(repo / "fake_converted" / problem, target)
    (target / "README.md").write_text(f"converted at {time.time_ns()}\\n")
    return 0
"""
# Mirrors the pinned entry script: puts its own dir on sys.path, re-exports main.
FAKE_CONVERTER_ENTRY = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scb_to_harbor_lib.cli import main
"""


# An upstream checkpoint spec: a leading comment (kept verbatim), both entrypoint tokens, and
# a final line without a newline.
UPSTREAM_SPEC = (
    "<!-- upstream header -->\n"
    "# {step}\n\n"
    "Run `%%%ENTRYPOINT:entry_command%%% --json` (the file is `%%%ENTRYPOINT:entry_file%%%`).\n"
    "Keep other syntax: %%%ENTRYPOINT:other%%%.\n"
    "No trailing newline"
)


def _write_converted(root: Path) -> None:
    (root).mkdir(parents=True)
    (root / "task.toml").write_text(CONVERTED_TASK_TOML)
    (root / "environment").mkdir()
    (root / "environment" / "Dockerfile").write_text("FROM python:3.12-slim\nWORKDIR /app\n")
    tests = write_tests(root / "tests")
    (tests / "_ctrf_to_reward.py").write_text("print('reward shim')\n")
    for index, step in enumerate(("checkpoint_1", "checkpoint_2")):
        step_dir = root / "steps" / step
        (step_dir / "tests").mkdir(parents=True)
        prior = "/tests/test_checkpoint_1.py" if index else ""
        (step_dir / "tests" / "test.sh").write_text(
            CONVERTED_TEST_SH.format(step=step, prior=prior, include_prior="true")
        )
        (step_dir / "instruction.md").write_text("converter-emitted text, never the agent's spec\n")
        write_app(step_dir / "solution" / "payload", mul=bool(index))
        (step_dir / "solution" / "solve.sh").write_text("#!/bin/bash\ncp -a payload/. /app/\n")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, timeout=120
    ).stdout.strip()


def write_fake_scb_tree(root: Path) -> Path:
    """The scb-problems working tree (no git) with a stand-in converter; returns its root."""
    repo = root / "scb-problems"
    (repo / "adder").mkdir(parents=True)
    (repo / "adder" / "config.yaml").write_text(
        "entry_file: app\ncheckpoints:\n  checkpoint_1: {}\n  checkpoint_2: {}\n"
    )
    for step in ("checkpoint_1", "checkpoint_2"):
        (repo / "adder" / f"{step}.md").write_text(UPSTREAM_SPEC.format(step=step))
    lib = repo / "scripts" / "scb_to_harbor_lib"
    lib.mkdir(parents=True)
    (lib / "__init__.py").write_text("")
    (lib / "cli.py").write_text(FAKE_CONVERTER_CLI)
    (repo / "scripts" / "scb_to_harbor.py").write_text(FAKE_CONVERTER_ENTRY)
    _write_converted(repo / "fake_converted" / "adder")
    return repo


def build_fake_scb_repo(root: Path) -> tuple[Path, str]:
    """A git repo shaped like scb-problems with a stand-in converter; returns (repo, commit)."""
    repo = write_fake_scb_tree(root)
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def scripted_tree_id(path: str) -> str:
    """The tree id :class:`ScriptedSource` reports for a checkout-relative path."""
    return hashlib.sha1(path.encode()).hexdigest()


class ScriptedSource:
    """An injected ``subprocess.run`` standing in for git and the pinned converter.

    It answers the importer's git queries for the tree built by :func:`write_fake_scb_tree`
    and "runs" the converter by copying that tree's ``fake_converted`` task, so a whole import
    stays in memory.  ``dirty`` is the ``git status`` output, ``converter_exit`` the converter's
    exit code, and ``hang`` makes the converter time out.
    """

    def __init__(
        self,
        repo: Path,
        commit: str,
        *,
        head: str | None = None,
        dirty: str = "",
        converter_exit: int = 0,
        hang: bool = False,
        clone_error: str | None = None,
    ) -> None:
        self.repo, self.commit = repo, commit
        self.head = commit if head is None else head
        self.dirty, self.converter_exit, self.hang = dirty, converter_exit, hang
        self.clone_error = clone_error
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        self.calls.append(args)
        if args[0] == sys.executable:
            return self._convert(args)
        if args[1] == "clone":
            return self._clone(args)
        return self._git(args[3:])

    def _done(self, args: list[str], code: int = 0, out: str = "", err: str = "") -> Any:
        return subprocess.CompletedProcess(args, code, out, err)

    def _git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[0] == "rev-parse" and args[1] == "HEAD":
            return self._done(args, out=self.head + "\n")
        if args[0] == "rev-parse":
            path = args[1].split(":", 1)[1]
            if (self.repo / path).exists():
                return self._done(args, out=scripted_tree_id(path) + "\n")
            return self._done(args, code=128, err="fatal: path does not exist")
        if args[0] == "status":
            return self._done(args, out=self.dirty)
        return self._done(args)  # checkout

    def _clone(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if self.clone_error is not None:
            return self._done(args, code=128, err=self.clone_error)
        destination = Path(args[-1])
        shutil.copytree(self.repo, destination)
        (destination / ".git").mkdir()
        return self._done(args)

    def _convert(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if self.hang:
            raise subprocess.TimeoutExpired(args, 600)
        out, problem = Path(args[-1]), args[-2]
        if self.converter_exit != 0:
            return self._done(args, self.converter_exit, "", "converter said no")
        target = out / "scb" / problem
        shutil.copytree(self.repo / "fake_converted" / problem, target)
        (target / "README.md").write_text("converted\n")
        return self._done(args)


def fake_store(root: Path, commit: str) -> ProblemStore:
    root.mkdir(parents=True, exist_ok=True)
    (root / "sources.yaml").write_text(
        f"sources:\n  scb:\n    url: file:///nowhere\n    commit: {commit}\n"
    )
    return ProblemStore(root)
