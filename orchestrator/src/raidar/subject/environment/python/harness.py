"""Tie an experiment's harness version to the pack image pin.

Harbor's installed codex and claude-code agents skip ``install()`` only when
the configured ``version`` equals the version the binary reports, and reinstall
otherwise. The pack image bakes the pinned version in, so a trial on the pack
image stays on the warm path only when its agent is configured with that pin
and Harbor's own version check, run in the image, parses exactly that pin.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from harbor.agents.factory import AgentFactory
from harbor.agents.installed.base import BaseInstalledAgent
from harbor.models.agent.name import AgentName

from raidar.subject.environment.python.models import HarnessPin, PackSpec


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


def _harbor_agent(pin: HarnessPin) -> BaseInstalledAgent:
    agent_class = AgentFactory.get_agent_class(AgentName(pin.harbor_agent))
    if not issubclass(agent_class, BaseInstalledAgent):
        raise HarnessPinError(f"Harbor agent {pin.harbor_agent!r} is not an installed agent")
    # Construction only records its arguments; logs_dir is never touched here.
    return agent_class(logs_dir=Path(tempfile.gettempdir()), version=pin.version)


def harbor_version_command(pin: HarnessPin) -> str:
    """The shell command Harbor's install() runs to decide whether to reinstall.

    That is the agent's ``_INSTALL_VERSION_COMMAND`` (codex, claude-code), not
    the public ``get_version_command()``; the latter is only a fallback for an
    agent that defines no install-time command.
    """
    agent = _harbor_agent(pin)
    command = getattr(agent, "_INSTALL_VERSION_COMMAND", None) or agent.get_version_command()
    if not isinstance(command, str) or not command:
        raise HarnessPinError(f"Harbor agent {pin.harbor_agent!r} has no version command")
    return command


def harbor_reported_version(pin: HarnessPin, output: str, exit_code: int = 0) -> str:
    """What Harbor's install check makes of the version command's result.

    ``output`` is stdout and stderr merged, as Harbor execs. A non-zero exit
    makes Harbor reinstall before it parses anything, so it reports no version
    (the exit status, which never equals a pin).
    """
    if exit_code != 0:
        return f"exit status {exit_code}"
    return _harbor_agent(pin).parse_version(output)
