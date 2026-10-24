"""Build the ``JevRuntime`` a scorer run uses, rooted in its assessment store.

Audit records go to ``<store>/jev-audit`` and cached decisions to
``<store>/jev-cache``, both beside the Assessment objects, never in the run
directory and never in ``~/.omp``. The key comes from ``OPENROUTER_API_KEY``;
without it, uncached decisions fail as ``missing_key`` and are audited as such.
``replay_only`` never calls the provider: uncached decisions fail as
``cache_miss``, which is how a rescore proves it costs $0.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from raidar.scoring.judge.audit import AuditLog
from raidar.scoring.judge.cache import DecisionCache
from raidar.scoring.judge.client import JevClient, Transport, api_key_from_env
from raidar.scoring.judge.policies import POLICY_DIR, PolicyRegistry
from raidar.scoring.judge.runtime import JevRuntime

CACHE_DIR = "jev-cache"


def open_runtime(
    store_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
    replay_only: bool = False,
    trial: str | None = None,
    overrides: Mapping[str, str] | None = None,
    policy_dir: Path = POLICY_DIR,
    transport: Transport | None = None,
) -> JevRuntime:
    registry = PolicyRegistry.from_dir(policy_dir).with_overrides(overrides or {})
    api_key = None if replay_only else api_key_from_env(environ)
    client = JevClient(api_key, transport=transport) if api_key else None
    return JevRuntime(
        registry,
        audit=AuditLog.for_store(store_root),
        cache=DecisionCache(store_root / CACHE_DIR),
        client=client,
        replay_only=replay_only,
        trial=trial,
    )
