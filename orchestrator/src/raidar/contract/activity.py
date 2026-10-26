"""Host-work windows shared by producers and the generic agent-idle monitor.

Only names in the registry may suspend agent-idle accounting. The producer enforces
its operation's registered limit; the monitor retains the window and resets its
baseline even when an entire operation fits between two samples.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from weakref import WeakValueDictionary

HOST_LIMITS = frozenset({"request.judge", "idle.gate", "idle.tool", "idle.install"})


@dataclass
class HostActivity:
    clock: Callable[[], float] = time.monotonic
    observed_generation: int = 0
    generation: int = 0
    active: list[dict[str, Any]] = field(default_factory=list)
    windows: list[dict[str, Any]] = field(default_factory=list)


_ACTIVITIES: WeakValueDictionary[int, HostActivity] = WeakValueDictionary()


def observe_host_activity(environment: object, clock: Callable[[], float]) -> HostActivity:
    """Bind the phase's host activity without retaining a finished environment."""
    activity = HostActivity(clock)
    _ACTIVITIES[id(environment)] = activity
    return activity


@contextmanager
def host_window(environment: object, name: str, limit_s: float) -> Iterator[None]:
    """Mark bounded non-agent work; no sandbox state or credentials cross this boundary."""
    if name not in HOST_LIMITS:
        raise ValueError(f"unregistered host limit: {name}")
    if not math.isfinite(limit_s) or limit_s <= 0:
        raise ValueError("host limit must be finite and positive")
    activity = _ACTIVITIES.get(id(environment))
    if activity is None:
        yield
        return
    window: dict[str, Any] = {
        "limit": name,
        "limit_s": limit_s,
        "started": activity.clock(),
    }
    activity.generation += 1
    activity.active.append(window)
    activity.windows.append(window)
    try:
        yield
    finally:
        window["finished"] = activity.clock()
        window["seconds"] = round(window["finished"] - window["started"], 3)
        activity.active.remove(window)
        activity.generation += 1
