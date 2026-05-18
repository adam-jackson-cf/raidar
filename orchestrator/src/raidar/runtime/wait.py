"""Named wait helpers for runtime retry boundaries."""

from __future__ import annotations

import time

HARBOR_RATE_LIMIT_RETRY_DELAY_SEC = 20


def wait_for_cache_lock_retry() -> None:
    time.sleep(0.1)


def wait_for_remove_tree_retry(delay_sec: float) -> None:
    time.sleep(delay_sec)


def wait_for_harbor_rate_limit_retry() -> None:
    time.sleep(HARBOR_RATE_LIMIT_RETRY_DELAY_SEC)
