"""Draft resolution: whether package and workflow releases resolve from the working tree.

Smoke experiments only (``raidar lock --draft``). A draft resolves unregistered or edited packages
and workflows without the register and is marked in its lock, so it never admits a measured run.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_DRAFT: ContextVar[bool] = ContextVar("raidar_draft_resolution", default=False)


def drafting() -> bool:
    """Whether releases resolve from the working tree (smoke drafts)."""
    return _DRAFT.get()


def set_draft_resolution(enabled: bool) -> None:
    """Switch draft resolution for this process (a draft lock's plan and run)."""
    _DRAFT.set(enabled)


@contextmanager
def draft_resolution(enabled: bool = True) -> Iterator[None]:
    """Draft resolution for the block (``build_lock(draft=True)``, tests)."""
    token = _DRAFT.set(enabled)
    try:
        yield
    finally:
        _DRAFT.reset(token)
