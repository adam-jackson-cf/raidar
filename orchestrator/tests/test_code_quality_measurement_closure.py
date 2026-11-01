"""Measurement processes fail with a fork eligibility code and the pinned interpreter is reused."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raidar.scoring.code_quality import measurement
from raidar.scoring.code_quality.measurement import (
    MeasurementError,
    run_process,
)


def _fake_run(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        calls.append({"argv": argv, **kwargs})
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(measurement.subprocess, "run", run)
    return calls


def test_run_process_collects_streams_under_a_read_only_filtered_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_TOKEN", "hunter2")
    monkeypatch.setenv("PATH", "/bin")
    calls = _fake_run(monkeypatch, SimpleNamespace(returncode=3, stdout="out", stderr="err"))

    result = run_process(["tool", "x"], Path("/snap"), "stdin", 5.0)

    assert (result.returncode, result.stdout, result.stderr) == (3, "out", "err")
    env = calls[0]["env"]
    assert "SECRET_TOKEN" not in env
    assert env["PATH"] == "/bin"
    assert env["RUFF_NO_CACHE"] == "true"


def test_run_process_timeout_is_an_environment_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch, subprocess.TimeoutExpired(["tool"], 1.0))

    with pytest.raises(MeasurementError, match="measurement_environment_mismatch: .*timed out"):
        run_process(["tool"], None, None, 1.0)


def test_run_process_missing_interpreter_is_an_environment_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_run(monkeypatch, FileNotFoundError("no python"))

    with pytest.raises(MeasurementError, match="measurement_environment_mismatch: .*unavailable"):
        run_process(["tool"], None, None, 1.0)
