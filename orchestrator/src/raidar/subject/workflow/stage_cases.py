"""The status of a stop fixture's result (the data contract of fixtures is ``raidar_contract``)."""

from __future__ import annotations

from raidar.subject.primitive.canary import FixtureRun
from raidar.subject.primitive.stop import parse_result


def result_status(stdout: bytes, tokens: tuple[str, ...]) -> str:
    """The status of a stop result's ``stdout``; ``ValueError`` names what makes it unusable."""
    return parse_result(stdout, tokens).status


def stop_status(run: FixtureRun, tokens: tuple[str, ...]) -> tuple[str | None, str | None]:
    """(status, why not): the parsed stop status of a fixture call."""
    if not run.usable:
        return None, f"call unusable (exit {run.record.get('exit_code')})"
    stdout = str(run.record.get("stdout", "")).encode()
    try:
        return result_status(stdout, tokens), None
    except ValueError as error:
        return None, str(error)
