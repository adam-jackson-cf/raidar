"""Tests for experiment-level unscored rerun behavior."""

from types import SimpleNamespace

import click

from raidar import cli
from raidar.runner import StarterPreflightError


def _unscored_run(unscored: bool) -> SimpleNamespace:
    return SimpleNamespace(scores=SimpleNamespace(unscored=unscored))


def test_run_with_unscored_reruns_reruns_only_once(monkeypatch):
    calls: list[int] = []

    def fake_execute_repeat_batch(*, request, batch_size, repeat_parallel, start_index):
        calls.append(batch_size)
        if len(calls) == 1:
            return [_unscored_run(True), _unscored_run(True)]
        return [_unscored_run(True), _unscored_run(True)]

    monkeypatch.setattr(cli, "_execute_repeat_batch", fake_execute_repeat_batch)

    runs, retries_used, unresolved_unscored = cli._run_with_unscored_reruns(
        request=SimpleNamespace(),
        repeats=2,
        repeat_parallel=1,
        rerun_unscored=1,
    )

    assert len(calls) == 2
    assert retries_used == 1
    assert unresolved_unscored == 2
    assert len(runs) == 4


def test_run_with_unscored_reruns_skips_rerun_when_budget_zero(monkeypatch):
    calls: list[int] = []

    def fake_execute_repeat_batch(*, request, batch_size, repeat_parallel, start_index):
        calls.append(batch_size)
        return [_unscored_run(True), _unscored_run(True)]

    monkeypatch.setattr(cli, "_execute_repeat_batch", fake_execute_repeat_batch)

    runs, retries_used, unresolved_unscored = cli._run_with_unscored_reruns(
        request=SimpleNamespace(),
        repeats=2,
        repeat_parallel=1,
        rerun_unscored=0,
    )

    assert len(calls) == 1
    assert retries_used == 0
    assert unresolved_unscored == 2
    assert len(runs) == 2


def test_cleanup_stale_harbor_before_runs_invokes_full_cleanup(monkeypatch):
    called: dict[str, bool] = {}

    def fake_cleanup(*, include_containers: bool, include_build_processes: bool) -> None:
        called["include_containers"] = include_containers
        called["include_build_processes"] = include_build_processes

    monkeypatch.setattr(
        cli,
        "_runner_api",
        lambda: SimpleNamespace(cleanup_stale_harbor_resources=fake_cleanup),
    )

    cli._cleanup_stale_harbor_before_runs()

    assert called == {
        "include_containers": True,
        "include_build_processes": True,
    }


def test_run_with_unscored_reruns_abort_on_starter_preflight_error(monkeypatch):
    def fail_preflight(*, request, batch_size, repeat_parallel, start_index):
        raise StarterPreflightError("Starter preflight failed: bun run lint exited 1")

    monkeypatch.setattr(cli, "_execute_repeat_batch", fail_preflight)

    try:
        cli._run_with_unscored_reruns(
            request=SimpleNamespace(),
            repeats=2,
            repeat_parallel=1,
            rerun_unscored=1,
        )
    except click.ClickException as exc:
        assert "Fatal starter preflight error" in str(exc)
    else:
        raise AssertionError("Expected fatal starter preflight ClickException.")
