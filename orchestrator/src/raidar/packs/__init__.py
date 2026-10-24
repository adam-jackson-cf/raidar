"""Runtime packs: per-runtime agent images, tool locks and lazy provisioners.

A *runtime* is a language toolchain and equals a pack id (``python``,
``node-ts``, ...; F8). Every experiment declares exactly one runtime, and
everything that runs it (``lock``, ``plan``, ``run``) loads that runtime's
pack through ``load_runtime_pack`` rather than assuming Python. Only the
Python pack exists today; other runtimes are refused with a field-level
``RT6`` error at lock time.
"""

from __future__ import annotations

from collections.abc import Callable

from raidar.packs.python.models import PackSpec

__all__ = [
    "PackSpec",
    "UnknownPackError",
    "available_runtimes",
    "load_runtime_pack",
]


class UnknownPackError(LookupError):
    """No runtime pack is registered for the requested runtime."""


def _python_pack() -> PackSpec:
    from raidar.packs.python.models import load_pack_spec

    return load_pack_spec()  # the Python pack's own loader (pack.yaml beside it)


PACK_LOADERS: dict[str, Callable[[], PackSpec]] = {"python": _python_pack}


def available_runtimes() -> tuple[str, ...]:
    return tuple(sorted(PACK_LOADERS))


def load_runtime_pack(runtime: str) -> PackSpec:
    """The pack implementing ``runtime``; ``UnknownPackError`` when none is registered."""
    loader = PACK_LOADERS.get(runtime)
    if loader is None:
        raise UnknownPackError(
            f"no runtime pack for {runtime!r} (packs: {', '.join(available_runtimes())})"
        )
    pack = loader()
    if pack.id != runtime:
        raise UnknownPackError(f"the pack registered for {runtime!r} declares id {pack.id!r}")
    return pack
