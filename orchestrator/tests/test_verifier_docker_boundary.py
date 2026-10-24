"""Docker process boundary of the verifier: grader image, sandbox run/timeout, verifier wiring.

A fake ``docker`` executable first on PATH stands in for the Docker CLI so the
boundary behaviour (presence check before build, kill on timeout, failure
reporting) is asserted without a container runtime.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, cast

import pytest
from harbor.models.trial.paths import TrialPaths

from raidar.grading.bundle import GraderSpec
from raidar.grading.grader_image import ensure_grader_image, grader_dockerfile, grader_image_tag
from raidar.grading.regrade import docker_sandbox_factory
from raidar.grading.sandbox import DockerCopySandbox, SandboxRun, sweep_grader_orphans
from raidar.grading.verifier import RaidarVerifier
from tests.verifier_support import FakeAgentEnvironment, make_bundle

FAKE_DOCKER = """#!/bin/bash
state="$FAKE_DOCKER_STATE"
printf '%s\\n' "$*" >> "$state/calls"
shift 2  # --context <name>
case "$1" in
  image)
    [ -f "$state/image_present" ] || { echo "No such image"; exit 1; }
    echo "sha256:fakeimageid"; exit 0 ;;
  build)
    cat > "$state/dockerfile"
    [ -f "$state/build_fails" ] && { echo "build exploded"; exit 1; }
    touch "$state/image_present"; exit 0 ;;
  create)
    echo "$*" > "$state/create_argv"; exit 0 ;;
  cp)
    case "$2" in
      -) cat > "$state/copied_in.tar"; exit 0 ;;
      *) mkdir -p "$state/o/out" && tar -cf - -C "$state/o" out; exit 0 ;;
    esac ;;
  start)
    [ -f "$state/run_hangs" ] && exec sleep 30
    echo "graded"; exit 3 ;;
  kill)
    echo "$2" >> "$state/killed"; exit 0 ;;
  rm)
    echo "$3" >> "$state/removed"; exit 0 ;;
  container)
    exit 0 ;;
esac
exit 64
"""


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "docker"
    script.write_text(FAKE_DOCKER)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    state = tmp_path / "docker-state"
    state.mkdir()
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(state))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return state


def calls(state: Path) -> list[str]:
    return (state / "calls").read_text().splitlines()


def sandbox_run(tmp_path: Path, timeout_s: float = 30.0) -> SandboxRun:
    for name in ("app", "tests", "out"):
        (tmp_path / name).mkdir(exist_ok=True)
    return SandboxRun(
        app_dir=tmp_path / "app",
        tests_dir=tmp_path / "tests",
        out_dir=tmp_path / "out",
        argv=("python", "-m", "pytest"),
        env={},
        timeout_s=timeout_s,
        cpus=1.0,
        memory_mb=512,
    )


async def test_grader_image_is_built_once_then_presence_checked(fake_docker: Path) -> None:
    spec = GraderSpec(base_image="base:1", test_dependencies=("pyyaml",))
    tag = grader_image_tag(spec)

    cold = await ensure_grader_image(spec, docker_context="orbstack")
    warm = await ensure_grader_image(spec, docker_context="orbstack")

    assert (cold.tag, cold.built, warm.built) == (tag, True, False)
    assert (fake_docker / "dockerfile").read_text() == grader_dockerfile(spec)
    assert calls(fake_docker) == [
        f"--context orbstack image inspect {tag}",
        f"--context orbstack build -t {tag} -",
        f"--context orbstack image inspect {tag}",
    ]


async def test_grader_image_build_failure_is_reported(fake_docker: Path) -> None:
    (fake_docker / "build_fails").touch()
    with pytest.raises(RuntimeError, match="build exploded"):
        await ensure_grader_image(GraderSpec(base_image="base:1"), docker_context="orbstack")


async def test_sandbox_reports_exit_code_output_and_environment_id(
    tmp_path: Path, fake_docker: Path
) -> None:
    sandbox = DockerCopySandbox(image="grader:1", docker_context="orbstack")
    with pytest.raises(RuntimeError, match="not available"):
        await sandbox.environment_id()
    (fake_docker / "image_present").touch()

    result = await sandbox.run(sandbox_run(tmp_path))

    assert await sandbox.environment_id() == "sha256:fakeimageid"
    assert (result.exit_code, result.timed_out) == (3, False)
    assert result.output.startswith("graded")
    assert "[raidar-copy] copy_in_s=" in result.output
    sequence = [line.split()[2] for line in calls(fake_docker) if "image inspect" not in line]
    assert sequence == ["create", "cp", "start", "cp", "rm"]
    name = (fake_docker / "removed").read_text().strip()
    assert name.startswith("raidar-oracle-")
    assert f"--name {name} " in (fake_docker / "create_argv").read_text()


async def test_sandbox_timeout_kills_the_named_container(tmp_path: Path, fake_docker: Path) -> None:
    (fake_docker / "run_hangs").touch()

    result = await DockerCopySandbox(image="grader:1", docker_context="orbstack").run(
        sandbox_run(tmp_path, timeout_s=0.5)
    )

    assert result.timed_out
    assert result.exit_code is None
    killed = (fake_docker / "killed").read_text().split()
    assert killed[0].startswith("raidar-oracle-")
    assert (fake_docker / "removed").read_text().split() == killed  # removed even after a timeout


async def test_sweep_prunes_only_labelled_old_grader_containers(fake_docker: Path) -> None:
    sweep_grader_orphans("orbstack")
    assert calls(fake_docker) == [
        "--context orbstack container prune --force "
        "--filter label=raidar.role=grader --filter until=1h"
    ]


async def test_verifier_and_regrade_prepare_the_same_docker_grader(
    tmp_path: Path, fake_docker: Path
) -> None:
    bundle = make_bundle(tmp_path / "fixture")
    verifier = RaidarVerifier(
        oracle_bundle=str(bundle.root),
        docker_context="orbstack",
        task=cast(Any, None),
        trial_paths=TrialPaths(trial_dir=tmp_path / "trial"),
        environment=cast(Any, FakeAgentEnvironment()),
        step_name="checkpoint_1",
    )

    sandbox, grader = await verifier.prepare_sandbox()
    regrade_sandbox = await docker_sandbox_factory("orbstack")(bundle)

    assert grader.image == grader_image_tag(bundle.manifest.grader)
    assert grader.environment_id == "sha256:fakeimageid"
    assert grader.image_built
    assert (
        sandbox
        == regrade_sandbox
        == DockerCopySandbox(image=grader.image, docker_context="orbstack")
    )
