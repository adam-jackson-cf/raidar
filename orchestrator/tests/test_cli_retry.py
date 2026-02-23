"""Tests for suite-level void retry behavior."""

from types import SimpleNamespace

import click

from raidar import cli
from raidar.runner import ScaffoldPreflightError


def _void_run(voided: bool) -> SimpleNamespace:
    return SimpleNamespace(scores=SimpleNamespace(voided=voided))


def test_run_with_void_retries_retries_only_once(monkeypatch):
    calls: list[int] = []

    def fake_execute_repeat_batch(*, request, batch_size, repeat_parallel, start_index):
        calls.append(batch_size)
        if len(calls) == 1:
            return [_void_run(True), _void_run(True)]
        return [_void_run(True), _void_run(True)]

    monkeypatch.setattr(cli, "_execute_repeat_batch", fake_execute_repeat_batch)

    runs, retries_used, unresolved_void = cli._run_with_void_retries(
        request=SimpleNamespace(),
        repeats=2,
        repeat_parallel=1,
        retry_void=1,
    )

    assert len(calls) == 2
    assert retries_used == 1
    assert unresolved_void == 2
    assert len(runs) == 4


def test_run_with_void_retries_no_retry_when_budget_zero(monkeypatch):
    calls: list[int] = []

    def fake_execute_repeat_batch(*, request, batch_size, repeat_parallel, start_index):
        calls.append(batch_size)
        return [_void_run(True), _void_run(True)]

    monkeypatch.setattr(cli, "_execute_repeat_batch", fake_execute_repeat_batch)

    runs, retries_used, unresolved_void = cli._run_with_void_retries(
        request=SimpleNamespace(),
        repeats=2,
        repeat_parallel=1,
        retry_void=0,
    )

    assert len(calls) == 1
    assert retries_used == 0
    assert unresolved_void == 2
    assert len(runs) == 2


def test_cleanup_stale_harbor_before_runs_invokes_full_cleanup(monkeypatch):
    called: dict[str, bool] = {}

    def fake_cleanup(*, include_containers: bool, include_build_processes: bool) -> None:
        called["include_containers"] = include_containers
        called["include_build_processes"] = include_build_processes

    monkeypatch.setattr(cli, "cleanup_stale_harbor_resources", fake_cleanup)

    cli._cleanup_stale_harbor_before_runs()

    assert called == {
        "include_containers": True,
        "include_build_processes": True,
    }


def test_run_with_void_retries_aborts_on_scaffold_preflight_error(monkeypatch):
    def fail_preflight(*, request, batch_size, repeat_parallel, start_index):
        raise ScaffoldPreflightError("Scaffold preflight failed: bun run lint exited 1")

    monkeypatch.setattr(cli, "_execute_repeat_batch", fail_preflight)

    try:
        cli._run_with_void_retries(
            request=SimpleNamespace(),
            repeats=2,
            repeat_parallel=1,
            retry_void=1,
        )
    except click.ClickException as exc:
        assert "Fatal scaffold preflight error" in str(exc)
    else:
        raise AssertionError("Expected fatal scaffold preflight ClickException.")
