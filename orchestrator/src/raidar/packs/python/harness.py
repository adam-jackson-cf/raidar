"""Tie an experiment's harness version to the pack image pin.

Harbor's installed codex and claude-code agents skip ``install()`` only when
the configured ``version`` equals the version the binary reports, and reinstall
otherwise. The pack image bakes the pinned version in, so a trial on the pack
image stays on the warm path only when its agent is configured with that pin.
"""

from __future__ import annotations

from raidar.packs.python.models import PackSpec


class HarnessPinError(ValueError):
    """A harness is not pinned by the pack, or is configured off its pin."""


def pinned_harness_version(spec: PackSpec, harness: str, configured: str | None = None) -> str:
    """Return the pack pin for ``harness``; reject a configured version that differs.

    ``configured`` is the version an experiment asks Harbor to install. ``None``
    means the experiment takes the pin.
    """
    pin = spec.image.harnesses.get(harness)
    if pin is None:
        known = ", ".join(sorted(spec.image.harnesses))
        raise HarnessPinError(f"pack {spec.id} pins no harness {harness!r}; pinned: {known}")
    if configured is not None and configured != pin.version:
        raise HarnessPinError(
            f"{harness} is configured at {configured} but pack {spec.id} bakes in "
            f"{pin.version}; Harbor would reinstall it in every trial"
        )
    return pin.version


def harbor_agent_kwargs(
    spec: PackSpec, harness: str, configured: str | None = None
) -> dict[str, str]:
    """Harbor ``AgentConfig.kwargs`` that keep the agent on the baked-in harness."""
    return {"version": pinned_harness_version(spec, harness, configured)}
