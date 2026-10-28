"""``stop/1``: a package's check after each agent turn, with Raidar-owned bounds.

The stop call prints exactly ``{"status", "message", "tokens", "carry"}``. ``status`` is ``done``
(all declared outputs present and valid), ``resume`` (a nonempty message the agent is resumed
with, at most ``max_rounds`` times) or ``closed`` (terminal). Failures attribute by the canary
(public contract: canary attribution):

- watched state outside Raidar's bounds: agent outcome; the tool is not called;
- junk, schema error, overflow, crash, timeout, or ``done`` with an output missing: agent outcome
  when the canary passes (or the environment is exhausted), else the trial is invalid;
- either way one repair turn is allowed per package per checkpoint, then the package is closed.

Closing is irreversible: it writes a root-owned flag in the container and no later call is made.
Repairs do not consume the ``max_rounds`` budget of ordinary resumes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import JsonValue

from raidar.subject.primitive.calls import put_root_file
from raidar.subject.primitive.canary import Canary
from raidar.subject.primitive.outputs import OutputsResult, collect_outputs
from raidar.subject.primitive.site import Invocation, PackageSite, invoke

PRIMITIVE = "stop/1"
CLOSED_FLAGS = "/var/lib/raidar-calls/closed"
STATUSES = ("done", "resume", "closed")
Action = Literal["done", "resume", "repair", "closed"]
FIELDS = {"status", "message", "tokens", "carry"}


@dataclass(frozen=True)
class StopResult:
    """A valid stop result."""

    status: str
    message: str
    tokens: dict[str, str]
    carry: dict[str, JsonValue]


@dataclass(frozen=True)
class StopDecision:
    """What the checkpoint does next for one package, and why (host-written evidence)."""

    action: Action
    message: str
    reason: str
    record: dict[str, object]


@dataclass
class StopState:
    """One package's stop state across a checkpoint's turns."""

    rounds: int = 0
    repaired: bool = False
    closed: bool = False
    carry: dict[str, JsonValue] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=dict)
    decisions: list[dict[str, object]] = field(default_factory=list)


def _document(stdout: bytes) -> dict[str, Any]:
    """The JSON object in ``stdout`` with exactly the stop result's fields."""
    try:
        data = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"stop output is not JSON: {error}") from error
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise ValueError("stop output must have exactly status, message, tokens and carry")
    return data


def _check_tokens(tokens: object, declared: tuple[str, ...]) -> None:
    if not isinstance(tokens, dict) or not all(isinstance(v, str) for v in tokens.values()):
        raise ValueError("stop tokens must map names to strings")
    if set(tokens) - set(declared):
        raise ValueError(f"stop returned undeclared tokens {sorted(set(tokens) - set(declared))}")


def parse_result(stdout: bytes, declared: tuple[str, ...]) -> StopResult:
    """The stop result in ``stdout``; ``ValueError`` names what makes it unusable."""
    data = _document(stdout)
    status, message, tokens, carry = data["status"], data["message"], data["tokens"], data["carry"]
    if status not in STATUSES:
        raise ValueError(f"stop status {status!r} is not done, resume or closed")
    if not isinstance(message, str):
        raise ValueError("stop message must be a string")
    _check_tokens(tokens, declared)
    if not isinstance(carry, dict):
        raise ValueError("stop carry must be a JSON object")
    if status == "resume" and not message.strip():
        raise ValueError("a resume needs a nonempty message")
    return StopResult(status, message, tokens, carry)


def closed_flag_path(site: PackageSite) -> str:
    """Root-owned flag Raidar writes when this package's stop closes (scoped to the checkpoint)."""
    return f"{CLOSED_FLAGS}/{site.scope}-{site.evidence_dir.name}"


async def _close(
    site: PackageSite, state: StopState, reason: str, extra: dict[str, object]
) -> StopDecision:
    """Close the package for good: a root-owned flag, then no further calls."""
    state.closed = True
    await put_root_file(site.environment, closed_flag_path(site), reason.encode("utf-8"))
    return _decided(state, StopDecision("closed", "", reason, extra))


def _decided(state: StopState, decision: StopDecision) -> StopDecision:
    state.decisions.append(
        {"action": decision.action, "reason": decision.reason, **decision.record}
    )
    return decision


async def _unusable(
    site: PackageSite, state: StopState, reason: str, attribution: str, record: dict[str, object]
) -> StopDecision:
    """An agent-attributed failure: one repair turn, then closed."""
    record = {**record, "attribution": attribution}
    if state.repaired:
        return await _close(site, state, f"unusable after repair: {reason}", record)
    state.repaired = True
    message = (
        f"The {site.package.name} check could not use your work: {reason}. "
        "Repair what it reads, then finish."
    )
    return _decided(state, StopDecision("repair", message, reason, record))


async def _judge(
    site: PackageSite,
    state: StopState,
    result: StopResult,
    call: Invocation,
    outputs: OutputsResult,
    max_rounds: int,
) -> StopDecision | str:
    """Apply a valid result to the state machine; a ``str`` is why ``done`` is unusable.

    Only ``done`` owes outputs; a resume or close with absent outputs is still valid.
    """
    state.carry = result.carry
    state.tokens = result.tokens
    record: dict[str, object] = {
        "call": call.record(),
        "status": result.status,
        "outputs": outputs.record(),
    }
    if result.status == "closed":
        return await _close(site, state, "closed by the package", record)
    if result.status == "resume":
        if state.rounds >= max_rounds:
            return await _close(site, state, "resume beyond max_rounds", record)
        state.rounds += 1
        return _decided(state, StopDecision("resume", result.message, "requested", record))
    if outputs.problems:
        return _missing(outputs)
    return _decided(state, StopDecision("done", "", "reported", record))


def _missing(outputs: OutputsResult) -> str:
    return "done without valid outputs: " + ", ".join(
        f"{name} ({why})" for name, why in sorted(outputs.problems.items())
    )


async def run_stop(site: PackageSite, state: StopState, canary: Canary) -> StopDecision:
    """Run the package's stop once, after an agent turn, and decide what happens next.

    Raises ``TrialInvalid`` when the failure is the instrument's or an integrity failure.
    """
    stop = site.package.manifest.on_stop
    if stop is None or state.closed:
        return _decided(state, StopDecision("closed", "", "no stop call", {}))
    label = f"stop-{len(state.decisions)}"
    call = await invoke(
        site,
        PRIMITIVE,
        stop.run,
        stop.timeout_s,
        round_=state.rounds,
        carry=state.carry,
        label=label,
    )
    if call.result is None:
        return await _unusable(
            site,
            state,
            f"state outside bounds ({call.bounds_violation})",
            "agent_state",
            {"call": call.record()},
        )
    outputs = await collect_outputs(site, label)
    why = "stop call failed (exit, signal, timeout or output cap)"
    if call.result.usable:
        try:
            parsed = parse_result(call.result.stdout, site.package.manifest.token_names())
        except ValueError as error:
            why = str(error)
        else:
            judged = await _judge(site, state, parsed, call, outputs, stop.max_rounds)
            if isinstance(judged, StopDecision):
                return judged
            why = judged
    attribution = await canary.attribute_failure()
    return await _unusable(
        site,
        state,
        why,
        attribution,
        {"call": call.record(), "outputs": outputs.record(), "canary": canary.runs[-1]},
    )


def final_decision_record(state: StopState) -> Mapping[str, object]:
    """The package's stop state for ``workflow.json``."""
    return {
        "rounds": state.rounds,
        "repaired": state.repaired,
        "closed": state.closed,
        "carry": state.carry,
        "tokens": state.tokens,
        "decisions": state.decisions,
    }
