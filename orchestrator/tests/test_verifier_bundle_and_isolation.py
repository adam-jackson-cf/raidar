"""Verifier bundle import, snapshot identity, sandbox isolation and leak assertion (E1)."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from raidar.core.snapshot import (
    SNAPSHOT_EXCLUDES,
    app_artifact_config,
    copy_snapshot,
    tree_digest,
)
from raidar.verifier.bundle import (
    BUNDLE_MANIFEST,
    BundleError,
    BundleManifest,
    GraderSpec,
    VerifierStepSpec,
    build_bundle_from_scb_harbor_task,
    load_bundle,
)
from raidar.verifier.grader_image import GRADER_TOOLS, grader_dockerfile, grader_image_tag
from raidar.verifier.leak import LeakScanError, leak_scan_command, parse_leak_output
from raidar.verifier.sandbox import (
    CONTAINER_LAYOUT,
    RUNTIME_DIR,
    DockerCopySandbox,
    SandboxLayout,
    SandboxRun,
    render_pytest_args,
    sandbox_argv,
    sandbox_env,
)
from tests.verifier_support import make_bundle, step_spec

SCB_TEST_SH = """#!/bin/bash
set +e
INCLUDE_PRIOR={include}
PRIOR_TESTS=()
if [ "$INCLUDE_PRIOR" = "true" ]; then
  PRIOR_TESTS=({prior})
fi

"${{PYTEST_USER_PREFIX[@]}}" uv run \\
  --with pytest==9.0.3 \\
  --with pytest-json-ctrf==0.4.1 \\
  --with pytest-timeout==2.4.0 \\
  --with pyyaml \\
  -m pytest \\
    --timeout=30.0 \\
    --timeout-method=signal \\
    --basetemp="$PYTEST_TMP_ROOT" \\
    "${{PRIOR_TESTS[@]}}" \\
    /tests/test_{step}.py \\
    --checkpoint "{step}" \\
    --entrypoint "uv run --project /app forge.py" \\
    --ctrf=/tmp/ctrf.json \\
    --json-report \\
    --json-report-file=/tmp/pytest-report.json \\
    -rA
PYTEST_RC=$?
"""


AGENT_DOCKERFILE = "FROM example/uv:py3.12\nENV HOME=/tmp/agent_home\nRUN apt-get install -y git\n"


def write_scb_task(root: Path, *, include_prior: bool = True) -> Path:
    (root / "environment").mkdir(parents=True)
    (root / "environment" / "Dockerfile").write_text(AGENT_DOCKERFILE)
    (root / "tests").mkdir()
    for name in ("conftest.py", "test_checkpoint_1.py", "test_checkpoint_2.py"):
        (root / "tests" / name).write_text(f"# {name}\n")
    (root / "tests" / "_ctrf_to_reward.py").write_text("# shim\n")
    (root / "task.toml").write_text(
        textwrap.dedent(
            """
            [metadata]
            source_problem = "forge"
            [environment.env]
            TERM = "xterm"
            [[steps]]
            name = "checkpoint_1"
            [[steps]]
            name = "checkpoint_2"
            """
        )
    )
    for step, prior in (("checkpoint_1", ""), ("checkpoint_2", "/tests/test_checkpoint_1.py")):
        tests = root / "steps" / step / "tests"
        tests.mkdir(parents=True)
        (tests / "test.sh").write_text(
            SCB_TEST_SH.format(include="true" if include_prior else "false", prior=prior, step=step)
        )
    return root


# ------------------------------------------------------------------ bundle
def test_scb_task_import_keeps_the_validated_pytest_selection(tmp_path: Path) -> None:
    task = write_scb_task(tmp_path / "task")
    bundle = build_bundle_from_scb_harbor_task(task, tmp_path / "bundle", source={"sha": "abc"})
    manifest = bundle.manifest

    step2 = bundle.step("checkpoint_2")
    assert step2.pytest_args == (
        "--timeout=30.0",
        "--timeout-method=signal",
        "{tests}/test_checkpoint_1.py",
        "{tests}/test_checkpoint_2.py",
        "--checkpoint",
        "checkpoint_2",
        "--entrypoint",
        "uv run --project {app} forge.py",
    )
    assert step2.test_files == ("test_checkpoint_1.py", "test_checkpoint_2.py")
    assert step2.timeout_s == 30.0
    assert manifest.problem == "forge"
    assert manifest.grader == GraderSpec(
        base_image="example/uv:py3.12",
        environment_dockerfile=AGENT_DOCKERFILE,
        test_dependencies=("pyyaml",),
    )
    # The grader is the agent's environment plus the verifier tools, not just its base.
    assert grader_dockerfile(manifest.grader).startswith(AGENT_DOCKERFILE + "USER root\n")
    assert manifest.env == {"TERM": "xterm"}
    assert manifest.source["sha"] == "abc"
    assert not (bundle.tests_dir / "_ctrf_to_reward.py").exists()
    assert (bundle.root / BUNDLE_MANIFEST).is_file()


def test_scb_import_honours_include_prior_false(tmp_path: Path) -> None:
    task = write_scb_task(tmp_path / "task", include_prior=False)
    bundle = build_bundle_from_scb_harbor_task(task, tmp_path / "bundle")
    assert bundle.step("checkpoint_2").test_files == ("test_checkpoint_2.py",)


def test_scb_import_rejects_an_unrecognised_test_script(tmp_path: Path) -> None:
    task = write_scb_task(tmp_path / "task")
    (task / "steps" / "checkpoint_1" / "tests" / "test.sh").write_text("#!/bin/bash\nexit 0\n")
    with pytest.raises(BundleError, match="-m pytest"):
        build_bundle_from_scb_harbor_task(task, tmp_path / "bundle")


def test_step_spec_rejects_args_that_select_other_files() -> None:
    with pytest.raises(ValidationError, match="test_files"):
        VerifierStepSpec(
            name="s",
            checkpoint="s",
            test_files=("test_a.py",),
            pytest_args=("{tests}/test_b.py",),
        )


def test_manifest_rejects_duplicate_steps() -> None:
    step = step_spec("checkpoint_1", ["test_checkpoint_1.py"])
    with pytest.raises(ValidationError, match="duplicate"):
        BundleManifest(problem="p", grader=GraderSpec(base_image="x"), steps=(step, step))


def test_load_bundle_rejects_missing_test_files_and_unknown_steps(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    with pytest.raises(BundleError, match="no step"):
        bundle.step("checkpoint_9")
    (bundle.tests_dir / "test_checkpoint_2.py").unlink()
    with pytest.raises(BundleError, match="missing"):
        load_bundle(bundle.root)
    with pytest.raises(BundleError, match="no oracle-bundle.json"):
        load_bundle(tmp_path / "nowhere")


def test_hidden_fingerprints_skip_empty_files(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    fingerprints = bundle.hidden_file_hashes()
    assert "__init__.py" not in fingerprints.values()
    conftest = (bundle.tests_dir / "conftest.py").read_bytes()
    assert fingerprints[hashlib.sha256(conftest).hexdigest()] == "conftest.py"


# ---------------------------------------------------------------- snapshot
def test_snapshot_digest_ignores_excluded_paths_and_tracks_content(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "pkg").mkdir(parents=True)
    (app / "pkg" / "mod.py").write_text("x = 1\n")
    baseline = tree_digest(app)
    for excluded in (".venv", "__pycache__", "node_modules"):
        (app / "pkg" / excluded).mkdir()
        (app / "pkg" / excluded / "junk").write_text("noise")
    assert tree_digest(app) == baseline
    (app / "pkg" / "mod.py").write_text("x = 2\n")
    changed = tree_digest(app)
    assert changed != baseline
    (app / "pkg" / "mod.py").chmod(0o755)  # exec bit changes what a black-box test can run
    executable = tree_digest(app)
    assert executable != changed
    (app / "pkg" / "mod.py").chmod(0o777)  # read/write bits do not
    assert tree_digest(app) == executable


def test_snapshot_copy_drops_excludes_and_keeps_symlinks(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / ".git").mkdir(parents=True)
    (app / ".git" / "HEAD").write_text("ref")
    (app / "main.py").write_text("print(1)\n")
    os.symlink("main.py", app / "alias.py")
    copy = tmp_path / "copy"
    copy_snapshot(app, copy)
    assert sorted(p.name for p in copy.iterdir()) == ["alias.py", "main.py"]
    assert (copy / "alias.py").is_symlink()
    assert tree_digest(copy) == tree_digest(app)


def test_app_artifact_uses_the_same_exclude_list_as_the_digest() -> None:
    config = app_artifact_config()
    assert config["source"] == "/app"
    assert config["destination"] == "app"
    assert tuple(config["exclude"]) == SNAPSHOT_EXCLUDES  # type: ignore[arg-type]


# ------------------------------------------------------------------ sandbox
def test_docker_sandbox_is_offline_unprivileged_and_mounts_nothing(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    argv, _ = sandbox_argv(bundle.step("checkpoint_2"), CONTAINER_LAYOUT, coverage=False)
    run = SandboxRun(
        app_dir=tmp_path / "app",
        tests_dir=bundle.tests_dir,
        out_dir=tmp_path / "out",
        argv=argv,
        env={"A": "1"},
        timeout_s=10,
        cpus=1.5,
        memory_mb=2048,
    )
    docker = DockerCopySandbox(image="grader:tag", docker_context="orbstack").create_argv(
        run, "raidar-oracle-x"
    )
    joined = " ".join(docker)
    assert docker[:4] == ["docker", "--context", "orbstack", "create"]
    assert "--rm" not in docker
    assert "-v" not in docker
    assert str(tmp_path) not in joined
    assert "raidar.role=grader" in docker
    assert docker[docker.index("--network") + 1] == "none"
    assert docker[docker.index("--user") + 1] == "1000:1000"
    assert docker[docker.index("--cap-drop") + 1] == "ALL"
    assert docker[docker.index("--memory") + 1] == "2048m"
    assert docker[-len(argv) :] == list(argv)


def test_pytest_invocation_isolates_config_and_renders_placeholders() -> None:
    step = step_spec("checkpoint_2", ["test_checkpoint_1.py", "test_checkpoint_2.py"])
    args = render_pytest_args(step, CONTAINER_LAYOUT)
    assert args[:2] == ("-c", "/oracle/runtime/pytest.ini")
    assert "--confcutdir=/tests" in args
    assert args[4:6] == ("-p", "no:cacheprovider")
    assert "/tests/test_checkpoint_1.py" in args
    assert not any("{" in arg for arg in args)
    graded, prefix = sandbox_argv(step, CONTAINER_LAYOUT, coverage=False)
    measured, measured_prefix = sandbox_argv(step, CONTAINER_LAYOUT, coverage=True)
    assert prefix == ()
    # -P keeps the submission copy (the cwd) off the grader's sys.path.
    assert graded[:2] == (CONTAINER_LAYOUT.python, "-P")
    assert measured[: 2 + len(measured_prefix)] == (CONTAINER_LAYOUT.python, "-P", *measured_prefix)
    assert graded[-len(args) :] == measured[-len(args) :] == args


def test_grader_image_pins_tools_and_is_content_addressed() -> None:
    spec = GraderSpec(base_image="base:1", test_dependencies=("pyyaml",))
    dockerfile = grader_dockerfile(spec)
    assert dockerfile.startswith("FROM base:1\n")
    assert all(f'"{tool}"' in dockerfile for tool in GRADER_TOOLS)
    assert all("==" in tool for tool in GRADER_TOOLS)
    assert grader_image_tag(spec) == grader_image_tag(spec)
    assert grader_image_tag(spec) != grader_image_tag(GraderSpec(base_image="base:2"))


# --------------------------------------------------------------------- leak
def test_leak_scan_flags_copies_of_hidden_files_and_a_populated_tests_dir() -> None:
    hashes = {"a" * 64: "test_checkpoint_1.py"}
    clean = parse_leak_output(
        f"{'b' * 64}  /app/test_checkpoint_1.py\nRAIDAR_LEAK_SCAN_DONE\n", hashes, 0.1
    )
    assert clean.clean  # same name, different content: the agent's own file

    copied = parse_leak_output(
        f"{'a' * 64}  /tmp/x/test_checkpoint_1.py\nRAIDAR_LEAK_SCAN_DONE\n", hashes, 0.1
    )
    assert not copied.clean
    assert copied.hidden_copies == ("/tmp/x/test_checkpoint_1.py == test_checkpoint_1.py",)

    tests_dir = parse_leak_output("TESTS\t/tests/conftest.py\nRAIDAR_LEAK_SCAN_DONE\n", hashes, 0)
    assert not tests_dir.clean
    assert tests_dir.tests_dir_entries == ("/tests/conftest.py",)


def test_leak_scan_fails_closed_when_it_cannot_complete() -> None:
    with pytest.raises(LeakScanError):
        parse_leak_output("", {}, 0.0)
    with pytest.raises(LeakScanError):
        parse_leak_output("RAIDAR_LEAK_SCAN_UNAVAILABLE\nRAIDAR_LEAK_SCAN_DONE\n", {}, 0.0)


def test_leak_scan_command_quotes_names_and_prunes_virtual_filesystems() -> None:
    command = leak_scan_command(["test_a.py", "odd name.py", "test_a.py"])
    assert command.count("-name 'odd name.py'") == 1
    assert command.count("-name test_a.py") == 1
    assert "-path /proc" in command
    # Bind mounts (/logs/*, environment mounts) are scanned: never -xdev.
    assert "-xdev" not in command
    assert "find / " in command
    assert command.endswith("echo RAIDAR_LEAK_SCAN_DONE")


@pytest.mark.parametrize(
    ("breakage", "message"),
    [
        (lambda task: (task / "task.toml").write_text("[metadata]\n"), "no \\[\\[steps\\]\\]"),
        (lambda task: (task / "environment" / "Dockerfile").write_text("RUN true\n"), "no FROM"),
        (
            lambda task: (task / "environment" / "Dockerfile").write_text(
                "FROM base:1\nCOPY setup.sh /opt/\n"
            ),
            "COPY/ADD",
        ),
        (
            lambda task: (task / "steps" / "checkpoint_2" / "tests" / "test.sh").unlink(),
            "missing",
        ),
        (
            lambda task: (task / "steps" / "checkpoint_2" / "tests" / "test.sh").write_text(
                '-m pytest \\\n  "${PRIOR_TESTS[@]}" /tests/test_checkpoint_2.py\nPYTEST_RC=$?\n'
            ),
            "INCLUDE_PRIOR",
        ),
    ],
)
def test_scb_import_rejects_malformed_tasks(tmp_path: Path, breakage, message: str) -> None:
    task = write_scb_task(tmp_path / "task")
    breakage(task)
    with pytest.raises(BundleError, match=message):
        build_bundle_from_scb_harbor_task(task, tmp_path / "bundle")


def test_checkpoint_defaults_to_the_step_name_without_a_checkpoint_option(tmp_path: Path) -> None:
    task = write_scb_task(tmp_path / "task")
    test_sh = task / "steps" / "checkpoint_1" / "tests" / "test.sh"
    test_sh.write_text(test_sh.read_text().replace('--checkpoint "checkpoint_1" \\\n', ""))
    bundle = build_bundle_from_scb_harbor_task(task, tmp_path / "bundle")
    assert bundle.step("checkpoint_1").checkpoint == "checkpoint_1"
    assert "--checkpoint" not in bundle.step("checkpoint_1").pytest_args


def test_rebuilding_a_bundle_replaces_the_previous_one(tmp_path: Path) -> None:
    task = write_scb_task(tmp_path / "task")
    first = build_bundle_from_scb_harbor_task(task, tmp_path / "bundle")
    (first.tests_dir / "stale.py").write_text("stale\n")
    second = build_bundle_from_scb_harbor_task(task, tmp_path / "bundle")
    assert not (second.tests_dir / "stale.py").exists()
    assert second.digest() == build_bundle_from_scb_harbor_task(task, tmp_path / "b2").digest()


def test_invalid_manifest_is_a_bundle_error(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    (bundle.root / BUNDLE_MANIFEST).write_text('{"schema_version": 2}')
    with pytest.raises(BundleError, match="invalid"):
        load_bundle(bundle.root)


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv on PATH")
@pytest.mark.parametrize("with_lock", [False, True])
def test_uv_entrypoint_runs_offline_when_the_submission_declares_a_dev_group(
    tmp_path: Path, with_lock: bool
) -> None:
    # Pilot v2 forge-01 (aborted): a submission with `dev = ["pytest>=8"]` failed
    # every case because `uv run --project /app` tried to download pytest offline.
    app = tmp_path / "app"
    app.mkdir()
    (app / "pyproject.toml").write_text(
        '[project]\nname = "sub"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n'
        'dependencies = []\n\n[dependency-groups]\ndev = ["pytest>=8"]\n'
    )
    if with_lock:
        (app / "uv.lock").write_text(
            'version = 1\nrequires-python = ">=3.10"\n\n[[package]]\nname = "sub"\n'
            'version = "0.1.0"\nsource = { virtual = "." }\n'
        )
    (app / "prog.py").write_text("print('ran')\n")
    layout = SandboxLayout(
        app=str(app),
        tests=str(tmp_path / "tests"),
        runtime=str(RUNTIME_DIR),
        out=str(tmp_path / "out"),
        scratch=str(tmp_path / "scratch"),
        python=sys.executable,
    )
    env = sandbox_env(make_bundle(tmp_path), layout, coverage=False)
    assert env["UV_OFFLINE"] == "1"
    host = {k: v for k, v in os.environ.items() if not k.startswith("UV_") and k != "VIRTUAL_ENV"}
    result = subprocess.run(
        ["uv", "run", "--project", str(app), str(app / "prog.py")],
        cwd=app,
        env={**host, **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ran"
