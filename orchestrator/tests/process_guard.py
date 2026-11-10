"""Test tiers: the fast-tier process guard and the Docker readiness probe.

Loaded as a pytest plugin by ``tests/conftest.py``. Tiers are applied at file level
(``pytestmark``); a test with none of ``slow``/``quality``/``docker``/``security``/``perf`` is
fast. The guard denies real child processes to fast tests, fixture setup and teardown included,
through an audit hook (including asyncio and multiprocessing launch paths). Injected
fakes never reach the hook, so they stay allowed. The Docker probe runs once, on the first
selected docker test, never at import or collection.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from typing import Any

import pytest

TIER_MARKERS = ("slow", "quality", "docker", "security", "perf")  # registered in pyproject
PROCESS_EVENTS = frozenset(
    {
        "subprocess.Popen",
        "os.system",
        "os.posix_spawn",
        "os.fork",
        "os.forkpty",
        "os.exec",
        "os.spawn",
        "pty.spawn",
    }
)
REQUIRE_DOCKER = "RAIDAR_REQUIRE_DOCKER"

_state = {"deny": False, "test": ""}


class ProcessGuardError(RuntimeError):
    """A fast-tier test started a real process."""


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if _state["deny"] and event in PROCESS_EVENTS:
        raise ProcessGuardError(
            f"{_state['test']}: {event} starts a real process in the fast tier; "
            "inject a fake, or mark the file slow/quality/docker (see .omp/test-profile.md)"
        )


sys.addaudithook(_audit)


def is_fast(item: pytest.Item) -> bool:
    return not any(item.get_closest_marker(name) for name in TIER_MARKERS)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Iterator[None]:
    """Deny processes from before fixture setup until after teardown for fast tests."""
    _state["deny"] = is_fast(item)
    _state["test"] = item.nodeid
    try:
        yield
    finally:
        _state["deny"] = False
        _state["test"] = ""


_docker_ready: list[bool] = []


@pytest.fixture(autouse=True)
def _docker_target_gate(request: pytest.FixtureRequest) -> None:
    """Probe the selected Docker target and pack image once, for docker tests only.

    Missing target or image skips, except under ``RAIDAR_REQUIRE_DOCKER=1`` where it fails.
    """
    if request.node.get_closest_marker("docker") is None:
        return
    if not _docker_ready:
        from tests.docker_trial_support import docker_target_ready

        _docker_ready.append(docker_target_ready())
    if not _docker_ready[0]:
        reason = "needs a ready Docker target (data check passing) and the built python pack image"
        if os.environ.get(REQUIRE_DOCKER) == "1":
            pytest.fail(f"{REQUIRE_DOCKER}=1 but {reason}", pytrace=False)
        pytest.skip(reason)
