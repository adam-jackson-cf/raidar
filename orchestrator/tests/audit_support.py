"""A tiny two-checkpoint key-value problem whose tests assert both determined and
undetermined values, for the spec-determinacy audit and errata tests.

Checkpoint 1's specification states the not_found detail ("identifies the
requested key") but not the validation wording or any output byte format;
checkpoint 2 adds ``put`` whose conflict detail content is unstated.
"""

from __future__ import annotations

import hashlib
import sys
import textwrap
from pathlib import Path

from raidar.design.problem.content import content_digest
from raidar.design.problem.models import (
    PackBinding,
    ProblemManifest,
    ProblemMode,
    ProblemSource,
    ProblemStep,
)
from raidar.design.problem.store import ImportedProblem, ProblemStore
from raidar.grading.bundle import BundleManifest, GraderSpec, VerifierStepSpec, write_bundle

KV_VERSION = "ab" * 6
KV_REF = f"fixture/kv@{KV_VERSION}"

SPEC_1 = """
# Checkpoint 1

`kv get <key>` prints the stored value as a JSON object.

## Errors

Errors print a JSON object with `error` and `detail` fields and exit `1`.

| Category | Condition |
|---|---|
| `validation_error` | the key is empty |
| `not_found` | the key is not stored |

### Not Found Detail

The detail identifies the requested key.
"""

SPEC_2 = """
# Checkpoint 2

`kv put <key> <value>` stores a value. Storing an existing key is a `conflict` error.
"""

HELPERS = """
import json
import subprocess


def run(argv, *args):
    return subprocess.run([*argv, *args], capture_output=True, text=True, check=False)


def assert_error(result, *, category, detail=None, detail_contains=None):
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error"] == category
    if detail is not None:
        assert payload["detail"] == detail
    if detail_contains is not None:
        assert detail_contains in payload["detail"]
    return payload


def assert_compact(result):
    assert result.stdout == json.dumps(json.loads(result.stdout), separators=(",", ":"))
"""

CONFTEST = """
import shlex

import pytest


def pytest_addoption(parser):
    parser.addoption("--entrypoint", action="store", required=True)
    parser.addoption("--checkpoint", action="store", required=True)


def pytest_configure(config):
    for name in ("error", "functionality", "regression"):
        config.addinivalue_line("markers", f"{name}: scb group")


@pytest.fixture(scope="session")
def entrypoint_argv(request):
    return shlex.split(request.config.getoption("--entrypoint"))
"""

TEST_1 = """
import json

import pytest

from kv_helpers import assert_compact, assert_error, run


def test_get_known(entrypoint_argv):
    assert json.loads(run(entrypoint_argv, "get", "a").stdout) == {"value": "1"}


@pytest.mark.error
def test_get_missing_names_key(entrypoint_argv):
    assert_error(run(entrypoint_argv, "get", "k1"), category="not_found", detail_contains="k1")


@pytest.mark.error
def test_empty_key_wording(entrypoint_argv):
    assert_error(
        run(entrypoint_argv, "get", ""),
        category="validation_error",
        detail_contains="key must be a non-empty string",
    )


@pytest.mark.functionality
def test_output_is_compact(entrypoint_argv):
    assert_compact(run(entrypoint_argv, "get", "a"))
"""

TEST_2 = """
import pytest

from kv_helpers import assert_error, run


@pytest.mark.error
@pytest.mark.parametrize("key", ["a", "b"])
def test_put_conflict_detail_is_key(entrypoint_argv, key):
    payload = assert_error(run(entrypoint_argv, "put", key, "9"), category="conflict")
    detail = payload["detail"]
    assert detail == key
"""

APP = """
import json
import sys

STORE = {"a": "1", "b": "2"}


def fail(category, detail):
    print(json.dumps({"detail": detail, "error": category}, separators=(",", ":")), end="")
    return 1


def main(argv):
    op, key, *rest = argv
    if not key:
        return fail("validation_error", "key must be a non-empty string")
    if op == "get":
        if key not in STORE:
            return fail("not_found", key)
        print(json.dumps({"value": STORE[key]}, separators=(",", ":")), end="")
        return 0
    if op == "put" and PUT:
        return fail("conflict", key) if key in STORE else 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
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
            f"{sys.executable} {{app}}/kv.py",
        ),
        timeout_s=None,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip())


def build_kv_problem(root: Path) -> tuple[ProblemStore, ImportedProblem]:
    """Write the kv problem into a store at ``root`` and load it."""
    (root / "sources.yaml").parent.mkdir(parents=True, exist_ok=True)
    (root / "sources.yaml").write_text(
        f"sources:\n  fixture:\n    format: scb-problems\n    url: fixture://kv\n"
        f"    commit: {KV_VERSION * 3}{KV_VERSION[:4]}\n"
    )
    store = ProblemStore(root)
    content = root / "store" / "fixture" / "kv" / KV_VERSION
    tests = root / "work-tests"
    for name, text in {
        "conftest.py": CONFTEST,
        "kv_helpers.py": HELPERS,
        "test_checkpoint_1.py": TEST_1,
        "test_checkpoint_2.py": TEST_2,
    }.items():
        _write(tests / name, text)
    steps = {
        "checkpoint_1": _step("checkpoint_1", ["test_checkpoint_1.py"]),
        "checkpoint_2": _step("checkpoint_2", ["test_checkpoint_1.py", "test_checkpoint_2.py"]),
    }
    bundle = write_bundle(
        content / "oracle",
        BundleManifest(
            problem=KV_REF,
            source={"format": "test-fixture"},
            env={"TERM": "xterm"},
            grader=GraderSpec(base_image="local"),
            steps=tuple(steps.values()),
        ),
        tests,
    )
    for index, step in enumerate(steps, start=1):
        _write(content / "references" / step / "kv.py", APP.replace("PUT", str(index == 2)))
    task = content / "task"
    _write(task / "task.toml", 'schema_version = "1.1"\n')
    manifest_steps = []
    for step, text in (("checkpoint_1", SPEC_1), ("checkpoint_2", SPEC_2)):
        instruction = task / "steps" / step / "instruction.md"
        _write(instruction, text)
        manifest_steps.append(
            ProblemStep(
                name=step,
                checkpoint=step,
                instruction_sha256=hashlib.sha256(instruction.read_bytes()).hexdigest(),
                test_files=steps[step].test_files,
            )
        )
    store.write_manifest(
        ProblemManifest(
            ref=KV_REF,
            mode=ProblemMode.MULTI_CHECKPOINT,
            checkpoints=2,
            steps=tuple(manifest_steps),
            source=ProblemSource(
                dataset="fixture",
                url="fixture://kv",
                commit=f"{KV_VERSION * 3}{KV_VERSION[:4]}",
                problem="kv",
                checkpoint_selection=None,
                problem_tree="fixture",
                converter_tree="fixture",
                converted_task_sha256="fixture",
            ),
            pack=PackBinding(id="python", version=1, image="local"),
            task_sha256=content_digest(task),
            bundle_sha256=content_digest(bundle.root),
            references_sha256=content_digest(content / "references"),
            entry_file="kv.py",
        )
    )
    from raidar.design.problem.models import ProblemId

    return store, store.load(ProblemId.parse(KV_REF))
