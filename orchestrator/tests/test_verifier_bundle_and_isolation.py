"""Verifier bundle import, snapshot identity, sandbox isolation and leak assertion (E1)."""

from __future__ import annotations

import hashlib
import os
import textwrap
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from raidar.contract.snapshot import (
    SNAPSHOT_EXCLUDES,
    app_artifact_config,
    copy_snapshot,
    snapshot_files,
    tree_digest,
)
from raidar.grading.bundle import (
    BUNDLE_MANIFEST,
    BundleError,
    BundleManifest,
    GraderSpec,
    VerifierStepSpec,
    build_bundle_from_scb_harbor_task,
    load_bundle,
)
from raidar.grading.leak import (
    SCAN_TIMEOUT_S,
    LeakReport,
    LeakScanError,
    leak_scan_command,
    parse_leak_output,
    scan_agent_environment,
)
from raidar.grading.models import CanaryRecord, CaseGroup, CaseResult, CaseStatus, InfraFailure
from raidar.grading.records import VERIFIER_RECORD_DIR
from raidar.grading.regrade import (
    RegradeError,
    RegradeOptions,
    StepRegrade,
    TrialRegrade,
    compare,
    recorded_bundle,
    report_payload,
    stored_snapshot,
    trial_dirs,
    write_store_json,
)
from raidar.grading.sandbox import (
    CONTAINER_LAYOUT,
    render_pytest_args,
    sandbox_argv,
    sandbox_env,
)
from raidar.grading.verifier import (
    _artifact_status,
    _plumbing,
    artifact_unusable_reason,
    parse_artifact_status,
    step_leaked,
    step_rewards,
)
from tests.verifier_support import FakeAgentEnvironment, make_bundle, step_spec

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
    (app / ".claude" / "skills" / "workflow").mkdir(parents=True)
    (app / ".claude" / "skills" / "workflow" / "SKILL.md").write_text("workflow-only skill")
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


@pytest.mark.parametrize("suffix", ["", ".py", "\nodd"])
def test_snapshot_digest_distinguishes_record_boundaries(tmp_path: Path, suffix: str) -> None:
    app = tmp_path / "app"
    app.mkdir()
    first, second = app / f"a{suffix}", app / f"b{suffix}"
    first.write_bytes(b"x=1\n\0" + second.name.encode() + b"\0Fy=2\n")
    embedded = tree_digest(app)
    first.write_bytes(b"x=1\n")
    second.write_bytes(b"y=2\n")
    assert tree_digest(app) != embedded


def test_snapshot_digest_tracks_path_link_target_and_entry_kind(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    entry = app / "entry"
    entry.write_bytes(b"target")
    regular = tree_digest(app)
    entry.rename(app / "renamed")
    renamed = tree_digest(app)
    assert renamed != regular
    (app / "renamed").unlink()
    entry.symlink_to("target")
    link = tree_digest(app)
    assert link != regular
    entry.unlink()
    entry.symlink_to("other")
    assert tree_digest(app) != link


def test_snapshot_walk_refuses_missing_and_unreadable_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(FileNotFoundError):
        snapshot_files(tmp_path / "missing")
    app = tmp_path / "app"
    app.mkdir()
    private = app / "private"
    private.mkdir()
    (private / "app.py").write_text("x = 1\n")
    walk = os.walk

    def unreadable_walk(*args: object, **kwargs: object) -> object:
        onerror = kwargs["onerror"]
        assert callable(onerror)
        onerror(PermissionError("snapshot directory is unreadable"))
        return walk(*args, **kwargs)

    monkeypatch.setattr(os, "walk", unreadable_walk)
    with pytest.raises(PermissionError, match="unreadable"):
        tree_digest(app)


def test_snapshot_digest_refuses_special_files_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    os.mkfifo(app / "generated.py")

    def unexpected_open(*args: object, **kwargs: object) -> int:
        raise AssertionError("special snapshot entry must never be opened")

    monkeypatch.setattr(os, "open", unexpected_open)
    with pytest.raises(OSError, match="not a regular file"):
        tree_digest(app)


# ------------------------------------------------------------------ sandbox
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


def test_leak_scan_sees_a_copy_whose_path_sha256sum_escapes() -> None:
    """GNU sha256sum prefixes the line with a backslash when the path holds a backslash."""
    hashes = {"a" * 64: "test_checkpoint_1.py"}
    escaped = parse_leak_output(
        f"\\{'a' * 64}  /tmp/dir\\\\x/test_checkpoint_1.py\nRAIDAR_LEAK_SCAN_DONE\n", hashes, 0
    )
    assert not escaped.clean
    assert escaped.hidden_copies == ("/tmp/dir\\\\x/test_checkpoint_1.py == test_checkpoint_1.py",)


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


# ----------------------------------------------------------- verifier decisions
@pytest.mark.parametrize(
    ("text", "status"),
    [
        ('[{"source": "/app", "destination": "artifacts/app", "status": "ok"}]', "ok"),
        ('{"entries": [{"source": "/app", "destination": "x/app", "status": "failed"}]}', "failed"),
        (
            '[{"source": "/app", "destination": "x/app", "status": "failed"},'
            ' {"source": "/app", "destination": "x/app", "status": "ok"}]',
            "ok",  # the last record for the artifact wins
        ),
        ('[{"source": "/logs", "destination": "x/app", "status": "failed"}]', None),
        ('[{"source": "/app", "destination": "x/other", "status": "failed"}]', None),
        ('["not-an-entry", 3]', None),
        ("[]", None),
        ("{not json", "manifest_unreadable"),
        ("", "manifest_unreadable"),
    ],
)
def test_artifact_manifest_status_is_the_last_record_for_the_app_artifact(
    text: str, status: str | None
) -> None:
    assert parse_artifact_status(text) == status


@pytest.mark.parametrize(
    ("status", "is_dir", "reason"),
    [
        (None, True, None),  # older Harbor: a collected directory without a manifest
        (None, False, "artifact_missing"),
        ("ok", True, None),
        ("ok", False, None),
        ("failed", True, "artifact_failed"),
        ("empty", True, "artifact_empty"),
        ("manifest_unreadable", True, "artifact_manifest_unreadable"),
    ],
)
def test_only_a_collected_or_ok_artifact_is_gradable(
    status: str | None, is_dir: bool, reason: str | None
) -> None:
    assert artifact_unusable_reason(status, is_dir=is_dir) == reason


def test_artifact_status_reads_the_manifest_beside_the_app_directory(tmp_path: Path) -> None:
    app = tmp_path / "artifacts" / "app"
    assert _artifact_status(app) is None
    manifest = tmp_path / "artifacts" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text('[{"source": "/app", "destination": "artifacts/app", "status": "empty"}]')
    assert _artifact_status(app) == "empty"
    manifest.unlink()
    manifest.mkdir()  # present but unreadable as a file
    assert _artifact_status(app) is None
    manifest.rmdir()
    manifest.write_bytes(b"\xff\xfe")
    assert _artifact_status(app) == "manifest_unreadable"


@pytest.mark.parametrize(
    ("verify_clean", "pre_agent_clean", "leaked"),
    [
        (True, None, False),
        (True, True, False),
        (False, None, True),
        (False, True, True),
        (True, False, True),  # only the pre-agent scan can see a /tests copy baked into the image
        (False, False, True),
    ],
)
def test_a_step_leaks_when_either_scan_found_the_hidden_tests(
    verify_clean: bool, pre_agent_clean: bool | None, leaked: bool
) -> None:
    pre_agent = None if pre_agent_clean is None else LeakReport(clean=pre_agent_clean)
    assert step_leaked(LeakReport(clean=verify_clean), pre_agent) is leaked


def test_a_leaked_step_reports_only_the_leak_and_infra_flags() -> None:
    rewards = {"solved": 1.0, "infrastructure_failure": 0.0, "strict_pass_rate": 1.0}
    assert step_rewards(rewards, leaked=False) == {**rewards, "oracle_leak": 0.0}
    assert step_rewards(rewards, leaked=True) == {
        "infrastructure_failure": 0.0,
        "oracle_leak": 1.0,
    }


def test_plumbing_is_wall_time_outside_the_budgeted_phases() -> None:
    timings = {
        "verifier_total_s": 10.0,
        "leak_check_s": 1.0,
        "grader_image_s": 2.0,
        "snapshot_transfer_s": 0.5,
        "oracle_run_s": 3.0,
        "result_write_s": 0.25,  # a component of plumbing, never subtracted
    }
    assert _plumbing(timings) == 3.5
    assert _plumbing({**timings, "coverage_run_s": 0.5}) == 3.0
    assert _plumbing({"verifier_total_s": 1.0, "oracle_run_s": 5.0}) == 0.0  # never negative


# -------------------------------------------------------- leak scan, injected shell
class _Raising(FakeAgentEnvironment):
    async def exec(self, command: str, **kwargs: Any) -> Any:
        raise TimeoutError("scan timed out")


async def test_scan_reports_clean_leaked_and_unfinished_environments(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    digest = next(iter(bundle.hidden_file_hashes()))
    env = FakeAgentEnvironment()

    clean = await scan_agent_environment(env, bundle)
    assert clean.clean
    assert clean.fingerprints_checked == len(bundle.hidden_file_hashes())
    assert clean.seconds >= 0

    env.leak_stdout = f"{digest}  /app/stolen.py\nRAIDAR_LEAK_SCAN_DONE\n"
    leaked = await scan_agent_environment(env, bundle)
    assert not leaked.clean
    assert leaked.hidden_copies[0].startswith("/app/stolen.py == ")

    for unfinished in ("RAIDAR_LEAK_SCAN_UNAVAILABLE\n", "partial output", ""):
        env.leak_stdout = unfinished
        with pytest.raises(LeakScanError, match="did not complete"):
            await scan_agent_environment(env, bundle)


async def test_scan_runs_as_root_with_the_scan_budget_and_lets_a_timeout_propagate(
    tmp_path: Path,
) -> None:
    bundle = make_bundle(tmp_path)

    class Recording(FakeAgentEnvironment):
        seen: dict[str, Any] = {}

        async def exec(self, command: str, **kwargs: Any) -> Any:
            Recording.seen = {"command": command, **kwargs}
            return await super().exec(command, **kwargs)

    await scan_agent_environment(Recording(), bundle)
    assert Recording.seen["user"] == "root"
    assert Recording.seen["timeout_sec"] == SCAN_TIMEOUT_S
    assert "test_checkpoint_1.py" in Recording.seen["command"]

    with pytest.raises(TimeoutError):
        await scan_agent_environment(_Raising(), bundle)


async def test_scan_treats_a_missing_stdout_as_an_unfinished_scan(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)

    class NoStdout(FakeAgentEnvironment):
        async def exec(self, command: str, **kwargs: Any) -> Any:
            return SimpleNamespace(stdout=None, stderr="boom", return_code=127)

    with pytest.raises(LeakScanError):
        await scan_agent_environment(NoStdout(), bundle)


# ------------------------------------------------------------- regrade decisions
def _verdict(case_id: str, status: CaseStatus) -> CaseResult:
    return CaseResult(
        id=case_id, group=CaseGroup.CORE, checkpoint_origin="checkpoint_1", status=status
    )


def _stored(
    cases: tuple[CaseResult, ...] = (),
    *,
    environment_id: str = "img",
    infra: InfraFailure | None = None,
    parity: object = None,
    canary: CanaryRecord | None = None,
    dependencies: object = None,
) -> Any:
    return SimpleNamespace(
        grade=SimpleNamespace(cases=cases, infra_failure=infra),
        grader=SimpleNamespace(environment_id=environment_id),
        coverage=SimpleNamespace(parity=parity),
        canary=canary or CanaryRecord(),
        submission_dependencies=dependencies,
    )


def _regraded(
    cases: tuple[CaseResult, ...] = (),
    *,
    infra: InfraFailure | None = None,
    parity: object = None,
    canary: CanaryRecord | None = None,
    dependencies: object = None,
) -> Any:
    return SimpleNamespace(
        grade=SimpleNamespace(cases=cases, infra_failure=infra),
        parity=parity,
        canary=canary or CanaryRecord(),
        grading=SimpleNamespace(dependencies=dependencies),
    )


def _deps(image_id: str, lock_text: str = "lock") -> Any:
    return SimpleNamespace(image_id=image_id, lock_text=lock_text)


def test_a_regrade_that_reproduces_the_record_has_no_differences() -> None:
    cases = (_verdict("a", CaseStatus.PASSED), _verdict("b", CaseStatus.FAILED))
    assert compare(_stored(cases), _regraded(cases), "img") == ()


def test_each_kind_of_regrade_difference_is_named() -> None:
    old = (_verdict("a", CaseStatus.PASSED), _verdict("gone", CaseStatus.PASSED))
    new = (_verdict("a", CaseStatus.FAILED), _verdict("fresh", CaseStatus.PASSED))
    differences = compare(
        _stored(old, infra=None, canary=CanaryRecord(status="passed", trigger="sandbox_timeout")),
        _regraded(new, infra=InfraFailure(code="sandbox_error")),
        "other-img",
    )
    assert differences[:3] == (
        "gone: missing on regrade",
        "fresh: new on regrade",
        "a: passed -> failed",
    )
    assert "grader_environment: img -> other-img" in differences
    assert any(d.startswith("infra_failure: None -> ") for d in differences)
    assert "canary: passed/sandbox_timeout -> not_run/" in differences


def test_a_changed_parity_verdict_is_a_difference() -> None:
    differences = compare(_stored(parity=None), _regraded(parity="ineligible"), "img")
    assert differences == ("parity: None -> ineligible",)


@pytest.mark.parametrize(
    ("before", "after", "differs"),
    [
        (_deps("old"), _deps("new"), True),
        (_deps("same"), _deps("same"), False),
        (_deps("old", lock_text=""), _deps("new"), False),  # no closure was recorded
        (None, _deps("new"), False),
        (_deps("old"), None, False),
    ],
)
def test_a_rebuilt_closure_image_is_reported_only_for_a_recorded_closure(
    before: Any, after: Any, differs: bool
) -> None:
    differences = compare(_stored(dependencies=before), _regraded(dependencies=after), "img")
    expected = (f"closure_image: {before.image_id} -> {after.image_id}",) if differs else ()
    assert differences == expected


def test_trial_dirs_accepts_a_trial_or_a_job_of_trials(tmp_path: Path) -> None:
    trial = tmp_path / "trial-a"
    (trial / VERIFIER_RECORD_DIR).mkdir(parents=True)
    assert trial_dirs(trial) == [trial]
    (tmp_path / "trial-b" / VERIFIER_RECORD_DIR).mkdir(parents=True)
    (tmp_path / "unrelated").mkdir()
    assert trial_dirs(tmp_path) == [trial, tmp_path / "trial-b"]


def test_a_job_without_verifier_records_cannot_be_regraded(tmp_path: Path) -> None:
    (tmp_path / "trial-a").mkdir()
    with pytest.raises(RegradeError, match="records"):
        trial_dirs(tmp_path)


def test_stored_snapshot_must_exist_and_still_match_its_digest(tmp_path: Path) -> None:
    snapshot = tmp_path / "snap"
    snapshot.mkdir()
    (snapshot / "app.py").write_text("x = 1\n")
    stored = SimpleNamespace(stored_path="snap", digest=tree_digest(snapshot))
    record = SimpleNamespace(snapshot=stored)
    assert stored_snapshot(tmp_path, cast(Any, record)) == snapshot

    (snapshot / "app.py").write_text("x = 2\n")
    with pytest.raises(RegradeError, match="changed since grading"):
        stored_snapshot(tmp_path, cast(Any, record))
    missing = SimpleNamespace(snapshot=SimpleNamespace(stored_path="nope", digest="d"))
    with pytest.raises(RegradeError, match="stored snapshot missing"):
        stored_snapshot(tmp_path, cast(Any, missing))


def test_the_recorded_bundle_is_loaded_once_and_must_match_its_digest(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    record = SimpleNamespace(bundle=SimpleNamespace(root=str(bundle.root), digest=bundle.digest()))
    options = RegradeOptions(docker_context="ctx")

    first = recorded_bundle(cast(Any, record), options)
    assert recorded_bundle(cast(Any, record), options) is first
    assert list(options.bundle_cache) == [str(bundle.root)]

    override = RegradeOptions(docker_context="ctx", bundle_override=bundle.root)
    moved = SimpleNamespace(bundle=SimpleNamespace(root="/gone", digest=bundle.digest()))
    assert recorded_bundle(cast(Any, moved), override).digest() == bundle.digest()

    stale = SimpleNamespace(bundle=SimpleNamespace(root=str(bundle.root), digest="0" * 64))
    with pytest.raises(RegradeError, match="does not match recorded digest"):
        recorded_bundle(cast(Any, stale), RegradeOptions(docker_context="ctx"))


def test_the_regrade_report_matches_only_when_every_step_of_every_trial_does(
    tmp_path: Path,
) -> None:
    good = StepRegrade("s1", True, (), {"solved": True}, {"solved": True})
    bad = StepRegrade("s2", False, ("a: passed -> failed",), {"n": 1}, {"n": 2})
    report = report_payload(
        [TrialRegrade(tmp_path / "t1", (good,)), TrialRegrade(tmp_path / "t2", (good, bad))]
    )
    assert report["kind"] == "raidar-oracle-regrade"
    assert report["match"] is False
    trials = cast(list[dict[str, Any]], report["trials"])
    assert [t["match"] for t in trials] == [True, False]
    assert trials[1]["steps"][1] == {
        "step": "s2",
        "match": False,
        "differences": ["a: passed -> failed"],
        "original": {"n": 1},
        "regraded": {"n": 2},
    }
    assert report_payload([TrialRegrade(tmp_path / "t1", (good,))])["match"] is True
    assert report_payload([])["match"] is True


def test_store_files_are_replaced_atomically_with_sorted_json(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "grade.json"
    write_store_json(target, {"b": 1, "a": 2})
    assert target.read_text() == '{\n  "a": 2,\n  "b": 1\n}\n'
    write_store_json(target, {"c": 3})
    assert target.read_text() == '{\n  "c": 3\n}\n'
    assert [p.name for p in target.parent.iterdir()] == ["grade.json"]


def test_the_graded_step_is_named_by_harbor_or_is_the_only_step(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    assert bundle.resolve_step("checkpoint_2") == "checkpoint_2"
    with pytest.raises(BundleError, match="one-step"):
        bundle.resolve_step(None)  # two steps: Harbor must say which one is being graded

    only = bundle.manifest.model_copy(update={"steps": bundle.manifest.steps[:1]})
    assert replace(bundle, manifest=only).resolve_step(None) == "checkpoint_1"


def test_corpus_evidence_binds_the_tests_digest_to_the_selected_files(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    step = bundle.step("checkpoint_2")
    evidence = bundle.corpus_evidence(step)
    assert evidence.selected_tests == step.test_files
    assert evidence.corpus_sha256 == tree_digest(bundle.tests_dir)
    (bundle.tests_dir / "test_checkpoint_1.py").write_text("changed\n")
    assert bundle.corpus_evidence(step).corpus_sha256 != evidence.corpus_sha256


def test_a_step_without_a_timeout_option_has_no_case_timeout(tmp_path: Path) -> None:
    task = write_scb_task(tmp_path / "task")
    test_sh = task / "steps" / "checkpoint_1" / "tests" / "test.sh"
    test_sh.write_text(test_sh.read_text().replace("    --timeout=30.0 \\\n", ""))
    bundle = build_bundle_from_scb_harbor_task(task, tmp_path / "bundle")
    assert bundle.step("checkpoint_1").timeout_s is None
    assert bundle.step("checkpoint_2").timeout_s == 30.0


def test_the_grader_environment_is_offline_and_carries_the_oracle_channel(
    tmp_path: Path,
) -> None:
    bundle = make_bundle(tmp_path)
    layout = CONTAINER_LAYOUT
    graded = sandbox_env(bundle, layout, coverage=False)
    measured = sandbox_env(bundle, layout, coverage=True, budget_s=12.5, python_path=("/deps",))

    assert graded["UV_OFFLINE"] == "1"
    assert graded["UV_NO_SYNC"] == "1"  # the submission's own code, not its dev tooling
    assert graded["RAIDAR_ORACLE_LEDGER"] == "/oracle/out/ledger.json"
    assert graded["RAIDAR_ORACLE_COVERAGE"] == "0"
    assert "RAIDAR_ORACLE_BUDGET_S" not in graded
    assert measured["RAIDAR_ORACLE_COVERAGE"] == "1"
    assert measured["RAIDAR_ORACLE_BUDGET_S"] == "12.5"
    assert graded["TERM"] == "xterm"  # the bundle's own environment
    assert graded["PYTHONPATH"] == "/oracle/runtime"
    assert measured["PYTHONPATH"] == "/oracle/runtime:/deps"
    assert measured["RAIDAR_SUBMISSION_DEPS"] == "/deps"
