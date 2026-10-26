"""The Jev key stays on the host: no sandbox environment may carry it.

Raidar's Jev judge (``raidar.judge.live``) runs host-side with ``OPENROUTER_API_KEY``
from the process environment. A container's environment is built only from explicit sources
(task ``[environment].env``, the trial's persistent env, each ``exec -e``); the host process
environment reaches Compose only as its interpolation base. These checks make the guarantee
structural rather than incidental: a variable of that name, or any value equal to the host's
key (a task env of ``X = "${OPENROUTER_API_KEY}"`` resolves to it), is refused before the
container starts or a command runs. They compare names and a value's equality only, and the
refusal message and ``absence_evidence`` carry names, never values.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

JUDGE_KEY_ENV = "OPENROUTER_API_KEY"


class SecretIsolationError(RuntimeError):
    """The judge key was about to enter a sandbox environment."""


def leaked_names(env: Mapping[str, str], host: Mapping[str, str] | None = None) -> list[str]:
    """Names in ``env`` that are the judge key's name or carry the host's key value."""
    source = os.environ if host is None else host
    key = (source.get(JUDGE_KEY_ENV) or "").strip()
    return sorted(
        name for name, value in env.items() if name == JUDGE_KEY_ENV or (key and key in str(value))
    )


def require_key_absent(
    env: Mapping[str, str], where: str, host: Mapping[str, str] | None = None
) -> None:
    """Raise ``SecretIsolationError`` (names only) when ``env`` would carry the judge key."""
    names = leaked_names(env, host)
    if names:
        raise SecretIsolationError(
            f"the judge key must not enter the sandbox: {where} sets {', '.join(names)}"
        )


def exec_env(args: Sequence[str]) -> dict[str, str]:
    """The ``-e NAME=VALUE`` pairs of a compose ``exec`` argument list."""
    pairs: dict[str, str] = {}
    for index, arg in enumerate(args[:-1]):
        if arg in ("-e", "--env"):
            name, _, value = args[index + 1].partition("=")
            pairs[name] = value
    return pairs


def absence_evidence(
    sources: Mapping[str, Iterable[str]], host: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Names-only record that the judge key is not among the sandbox environment ``sources``.

    ``sources`` maps a source label (``task``, ``persistent``, ``setup``...) to the variable
    names it sets; the result lists them and states whether the host holds a key at all.
    """
    source = os.environ if host is None else host
    listed = {label: sorted(names) for label, names in sources.items()}
    return {
        "judge_key_env": JUDGE_KEY_ENV,
        "host_key_present": bool((source.get(JUDGE_KEY_ENV) or "").strip()),
        "sandbox_env_names": listed,
        "judge_key_in_sandbox": any(JUDGE_KEY_ENV in names for names in listed.values()),
    }
