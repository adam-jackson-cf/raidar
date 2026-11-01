"""The public smoke and registry make targets, read through real ``make`` invocations."""

from __future__ import annotations

import os
import re
import subprocess

import pytest
import yaml

from raidar.contract.paths import REPO_ROOT

pytestmark = pytest.mark.slow

SMOKE_PATH = "lab/experiments/smoke/python__circuit_eval-cp1__agent-health/experiment.yaml"
AGENTS_PATH = "lab/experiments/smoke/python__circuit_eval-cp1__pack-agent-health/experiment.yaml"


def _make_dry_run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", "-n", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _raidar_lines(output: str, command: str) -> list[str]:
    """Each ``raidar <command>`` invocation's arguments, continuation lines joined."""
    marker = f"raidar {command} "
    joined = re.sub(r"\\\n\s*", " ", output)
    return [line.split(marker, 1)[1] for line in joined.splitlines() if marker in line]


def test_orchestrator_smoke_locks_and_runs_the_smoke_experiment_with_the_same_overrides() -> None:
    result = _make_dry_run("orchestrator-smoke", "SMOKE_ATTEMPTS=2", "SMOKE_CONCURRENCY=2")

    assert result.returncode == 0, result.stderr
    lock_args = _raidar_lines(result.stdout, "lock")
    run_args = _raidar_lines(result.stdout, "run")
    assert len(lock_args) == len(run_args) == 1
    assert lock_args[0].startswith(f'"{SMOKE_PATH}" --output ')
    assert '--set "analysis.attempts=2"' in lock_args[0]
    assert run_args[0].startswith(f'"{SMOKE_PATH}" --lock ')
    assert '--set "analysis.attempts=2"' in run_args[0]
    assert '--concurrency "2"' in run_args[0]
    assert "orchestrator-smoke.lock.json" in lock_args[0]
    assert "orchestrator-smoke.lock.json" in run_args[0]


def test_agent_smoke_replaces_the_agent_release_model_and_reasoning() -> None:
    result = _make_dry_run("agent-smoke", "RELEASE=codex@3", "MODEL=gpt-6-luna", "TIMEOUT_SEC=300")

    assert result.returncode == 0, result.stderr
    (lock_args,) = _raidar_lines(result.stdout, "lock")
    (run_args,) = _raidar_lines(result.stdout, "run")
    for assignment in (
        "agents.0.release=codex@3",
        "agents.0.model=gpt-6-luna",
        "agents.0.reasoning=low",
        "protocol.agent_timeout_sec=300",
    ):
        assert f'--set "{assignment}"' in lock_args
        assert f'--set "{assignment}"' in run_args


@pytest.mark.parametrize(
    ("args", "effort"),
    [((), "low"), (("REASONING_EFFORT=",), ""), (("REASONING_EFFORT=medium",), "medium")],
)
def test_agent_smoke_reasoning_defaults_to_low_and_empty_means_unset(
    args: tuple[str, ...], effort: str
) -> None:
    result = _make_dry_run("agent-smoke", *args)

    assert result.returncode == 0, result.stderr
    (run_args,) = _raidar_lines(result.stdout, "run")
    assert f'--set "agents.0.reasoning={effort}"' in run_args


def test_smoke_agents_arm_runs_one_arm_of_the_agents_smoke() -> None:
    result = _make_dry_run("smoke-agents", "SMOKE_ARM=codex")

    assert result.returncode == 0, result.stderr
    (run_args,) = _raidar_lines(result.stdout, "run")
    assert run_args.startswith(f'"{AGENTS_PATH}"')
    assert '--arm "codex"' in run_args


def test_smoke_agents_arms_match_the_agents_smoke_experiment() -> None:
    agents_smoke = REPO_ROOT / AGENTS_PATH
    spec = yaml.safe_load(agents_smoke.read_text(encoding="utf-8"))
    assert spec["kind"] == "smoke"
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    arms = " ".join(arm["id"] for arm in spec["arms"])

    assert f"SMOKE_ARMS := {arms}\n" in makefile


@pytest.mark.parametrize("arm", ["unknown-agent", "claude"])
def test_smoke_agents_rejects_an_arm_the_experiment_does_not_have(arm: str) -> None:
    result = _make_dry_run("smoke-agents", f"SMOKE_ARM={arm}")

    assert result.returncode != 0
    assert f"Unknown SMOKE_ARM '{arm}'; expected one of: codex" in result.stderr


def test_smoke_dry_run_check_prints_every_public_smoke_shape() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "smoke-dry-run-check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    run_lines = _raidar_lines(result.stdout, "run")
    assert len(run_lines) == 4
    assert sum('--arm "' in line for line in run_lines) == 1


@pytest.mark.parametrize(
    ("args", "command", "expected"),
    [
        (("problem-list",), "problem", ["list"]),
        (
            ("problem-info", "PROBLEM=scb/forge@38d627ecf668"),
            "problem",
            ["info", '"scb/forge@38d627ecf668"'],
        ),
        (
            ("experiment-init", "EXPERIMENT=my-exp/experiment.yaml", "EXPERIMENT_ID=my-exp"),
            "init",
            ['"my-exp/experiment.yaml"', "--id", '"my-exp"'],
        ),
        (("stage-new", "NAME=ns/my-check"), "stage", ["new", '"ns/my-check"']),
        (("stage-lock", "NAME=ns/my-check"), "stage", ["lock", '"ns/my-check"']),
        (("stage-info", "NAME=ns/my-check"), "stage", ["info", '"ns/my-check"']),
        (("stage-list",), "stage", ["list"]),
        (
            ("stage-check", "NAME=ns/my-check", "REPLAY=var/jobs/t1"),
            "stage",
            ["check", '"ns/my-check"', "--replay", '"var/jobs/t1"'],
        ),
        (("stage-check", "NAME=ns/my-check"), "stage", ["check", '"ns/my-check"']),
        (("workflow-new", "NAME=acme/mine"), "workflow", ["new", '"acme/mine"']),
        (
            ("workflow-new", "NAME=acme/mine", "FROM=raidar/one-shot"),
            "workflow",
            ["new", '"acme/mine"', "--from", '"raidar/one-shot"'],
        ),
        (
            ("experiment-lock", "EXPERIMENT=e/experiment.yaml", "DRAFT=1"),
            "lock",
            ['"e/experiment.yaml"', "--draft"],
        ),
    ],
)
def test_registry_and_init_targets_call_their_raidar_verbs(
    args: tuple[str, ...], command: str, expected: list[str]
) -> None:
    result = _make_dry_run(*args)

    assert result.returncode == 0, result.stderr
    (arguments,) = _raidar_lines(result.stdout, command)
    assert arguments.split() == expected


@pytest.mark.parametrize(
    ("target", "variable"),
    [
        ("problem-info", "PROBLEM"),
        ("experiment-init", "EXPERIMENT"),
        ("stage-new", "NAME"),
        ("stage-lock", "NAME"),
        ("stage-info", "NAME"),
        ("workflow-new", "NAME"),
    ],
)
def test_registry_and_init_targets_require_their_argument(target: str, variable: str) -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", target],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert f"Missing required variable: {variable}" in result.stdout


def test_review_fixture_is_written_under_the_fixture_dir() -> None:
    result = _make_dry_run("review-fixture-synthetic")

    assert result.returncode == 0, result.stderr
    assert "tests.review_fixtures.projection" in result.stdout
    assert "/var/tmp/review-fixture" in result.stdout
    assert "experiments/" not in result.stdout


def test_review_surface_test_projects_both_fixture_roots_but_quality_only_one() -> None:
    result = _make_dry_run("review-surface-test")

    assert result.returncode == 0, result.stderr
    joined = re.sub(r"\\\n\s*", " ", result.stdout)
    review = next(
        line for line in joined.splitlines() if "raidar.evaluation.review.projection " in line
    )
    quality = next(
        line for line in joined.splitlines() if "raidar.evaluation.review.quality " in line
    )
    assert "--legacy-root" not in review
    assert "/var/tmp/review-fixture/review-only" in review
    assert "/var/tmp/review-fixture/jobs" in review
    assert "/var/tmp/review-fixture/review-only" not in quality


def test_review_surface_data_requires_jobs() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "review-surface-data", "REVIEW_JOBS_ROOT="],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Missing required variable: REVIEW_JOBS_ROOT" in result.stdout


def test_review_surface_test_applies_one_store_to_every_given_runs_root() -> None:
    result = _make_dry_run(
        "review-surface-test", "REVIEW_JOBS_ROOT=runs/a runs/b", "REVIEW_STORE=store"
    )

    assert result.returncode == 0, result.stderr
    joined = re.sub(r"\\\n\s*", " ", result.stdout)
    quality = next(
        line for line in joined.splitlines() if "raidar.evaluation.review.quality " in line
    )
    assert f'--jobs-root "{REPO_ROOT / "runs/a"}"' in quality
    assert f'--jobs-root "{REPO_ROOT / "runs/b"}"' in quality
    assert quality.count(f'--store "{REPO_ROOT / "store"}"') == 2


def test_review_surface_test_rejects_mismatched_runs_and_stores() -> None:
    result = _make_dry_run(
        "review-surface-test", "REVIEW_JOBS_ROOT=runs/a runs/b runs/c", "REVIEW_STORE=s1 s2"
    )

    assert result.returncode != 0
    assert "one store for all roots or one store per root" in result.stderr


@pytest.mark.parametrize(
    ("old", "new"),
    [("RUN=x", "JOB="), ("HARNESS=codex", "AGENT="), ("BLOCK=0", "ATTEMPT="), ("RUNS=x", "JOBS=")],
)
def test_renamed_make_variables_fail_with_their_new_name(old: str, new: str) -> None:
    result = _make_dry_run("help", old)

    assert result.returncode != 0
    assert f"was renamed {new}" in result.stderr


def test_renamed_make_variables_fail_from_the_environment_too() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "RUNS_DIR": "x"},
    )

    assert result.returncode != 0
    assert "RUNS_DIR= was renamed JOBS_DIR=" in result.stderr


@pytest.mark.skipif(
    (REPO_ROOT / "orchestrator" / ".env").exists(), reason="a local orchestrator/.env exists"
)
def test_secrets_dotenv_without_an_env_file_fails_before_running_anything() -> None:
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "agent-validate",
            "SECRETS=dotenv",
            "AGENT=codex",
            "PROVIDER=openai",
            "MODEL=gpt-6-luna",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "SECRETS=dotenv but orchestrator/.env does not exist" in result.stderr
    assert "secrets: orchestrator/.env" not in result.stderr
