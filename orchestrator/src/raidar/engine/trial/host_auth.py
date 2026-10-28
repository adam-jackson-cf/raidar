"""Host session auth for the codex agent of a trial's Harbor config."""

from __future__ import annotations

import os

from harbor.models.trial.config import AgentConfig

from raidar.engine.trial.agent_config import native_agent
from raidar.subject.agent.config import HARBOR_AGENT_NAMES, Harness
from raidar.subject.agent.native import credentials

CODEX_AUTH_ENV = "CODEX_AUTH_JSON_PATH"


def with_host_auth(config: AgentConfig, harness: Harness) -> AgentConfig:
    """Point Harbor's codex agent at the host's file-backed session auth, if configured.

    Only the file path is passed (presence-checked); the credential itself is
    never read here. Without a session file Harbor falls back to OPENAI_API_KEY.
    The workflow runner hands its env to every native turn, so every turn authenticates.
    """
    name, _ = native_agent(config)
    if name != HARBOR_AGENT_NAMES[Harness.CODEX_CLI] or harness is not Harness.CODEX_CLI:
        return config
    host = credentials(harness, os.environ)
    if host.issue is not None:
        raise OSError(host.issue)
    if not host.session_used or host.session_file is None:
        return config
    return config.model_copy(update={"env": {**config.env, CODEX_AUTH_ENV: str(host.session_file)}})
