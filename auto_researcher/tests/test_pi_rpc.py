"""Focused tests for PI RPC client message handling."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from selectors import BaseSelector
from types import SimpleNamespace
from typing import cast

from auto_researcher.pi_rpc import _PiRpcClient


class _FakeSelector:
    def select(self, timeout: float):  # noqa: ARG002
        return [object()]


def test_read_message_parses_jsonl_response() -> None:
    client = _PiRpcClient(
        pi_binary="pi",
        cwd=Path("/tmp"),
        session_dir=Path("/tmp"),
        timeout_sec=1,
    )
    client._process = cast(
        subprocess.Popen[str],
        SimpleNamespace(
            stdout=io.StringIO('{"type":"response","id":"cmd-1","success":true}\n'),
            stderr=io.StringIO(""),
        ),
    )
    client._selector = cast(BaseSelector, _FakeSelector())

    payload = client._read_message(timeout_sec=0.1)

    assert payload == {"type": "response", "id": "cmd-1", "success": True}
