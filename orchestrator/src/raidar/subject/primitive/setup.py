"""``setup/1``: run a package's setup before the agent and read the tokens it returns.

Setup runs on lab-owned inputs only. It prints exactly ``{"tokens": {NAME: "text", ...}}`` with a
value for every token the manifest declares and no other. A nonzero exit, a timeout, output over
the cap, invalid JSON, a missing or undeclared token, or state outside Raidar's bounds is an
instrument failure: the trial is invalid.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from raidar.subject.primitive.outputs import collect_outputs
from raidar.subject.primitive.site import PackageSite, TrialInvalid, invoke

PRIMITIVE = "setup/1"


@dataclass(frozen=True)
class SetupResult:
    """The tokens setup returned and the host-written record of the call."""

    tokens: dict[str, str]
    record: dict[str, object]


def parse_tokens(stdout: bytes, declared: Sequence[str]) -> dict[str, str]:
    """The tokens in setup's ``stdout``; ``ValueError`` names what is wrong with it."""
    try:
        data = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"setup output is not JSON: {error}") from error
    if not isinstance(data, dict) or set(data) != {"tokens"}:
        raise ValueError('setup output must be exactly {"tokens": {...}}')
    tokens = data["tokens"]
    if not isinstance(tokens, dict) or not all(isinstance(v, str) for v in tokens.values()):
        raise ValueError("setup tokens must map names to strings")
    missing = sorted(set(declared) - set(tokens))
    extra = sorted(set(tokens) - set(declared))
    if missing or extra:
        raise ValueError(f"setup tokens: missing {missing}, undeclared {extra}")
    return tokens


async def run_setup(site: PackageSite) -> SetupResult:
    """Run the package's setup; ``TrialInvalid`` when it cannot be relied on.

    A package without setup returns no tokens.
    """
    manifest = site.package.manifest
    if manifest.setup is None:
        return SetupResult({}, {"call": None})
    call = await invoke(
        site,
        PRIMITIVE,
        manifest.setup.run,
        manifest.setup.timeout_s,
        round_=0,
        carry={},
        label="setup",
    )
    if call.result is None:
        raise TrialInvalid("instrument", f"setup state refused: {call.bounds_violation}")
    result = call.result
    record = call.record()
    record["outputs"] = (await collect_outputs(site, "setup")).record()
    if not result.usable:
        raise TrialInvalid("instrument", "setup call failed (exit, timeout or output cap)", record)
    try:
        tokens = parse_tokens(result.stdout, manifest.token_names())
    except ValueError as error:
        raise TrialInvalid("instrument", str(error), record) from error
    return SetupResult(tokens, record)
