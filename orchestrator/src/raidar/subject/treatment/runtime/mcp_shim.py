"""Stdio MCP recorder used as the ``mcp-server`` discovery probe (runs in the container).

The ``mcp-server`` component launches the real server through this shim::

    python3 mcp_shim.py --log <jsonl> -- <command> [args...]

It relays newline-delimited JSON-RPC between the harness and the server
unchanged and appends one record per message to ``<jsonl>``: client requests
by method, server responses by the method they answer (with tool names for
``tools/list``). A harness that initialised the server and listed its tools
proves the server took effect. Standard library only.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import threading
from typing import IO, Any, cast

USAGE = "usage: mcp_shim.py --log <path> -- <command> [args...]"


def parse_args(argv: list[str]) -> tuple[str, list[str]]:
    if len(argv) < 4 or argv[0] != "--log" or argv[2] != "--":
        raise SystemExit(USAGE)
    return argv[1], argv[3:]


class Recorder:
    def __init__(self, log: IO[str]) -> None:
        self._log = log
        self._lock = threading.Lock()
        self._pending: dict[str, str] = {}

    def _write(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._log.write(json.dumps(record, sort_keys=True) + "\n")
            self._log.flush()

    def observe(self, direction: str, line: bytes) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            self._write({"dir": direction, "unparsed": True})
            return
        if not isinstance(message, dict):
            return
        if direction == "client" and isinstance(message.get("method"), str):
            self._observe_request(message)
        else:
            self._observe_response(direction, message)

    def _observe_request(self, message: dict[str, Any]) -> None:
        method = str(message["method"])
        if "id" in message:
            with self._lock:
                self._pending[json.dumps(message["id"])] = method
        self._write({"dir": "client", "method": method})

    def _observe_response(self, direction: str, message: dict[str, Any]) -> None:
        with self._lock:
            answered = self._pending.pop(json.dumps(message.get("id")), None)
        record: dict[str, Any] = {"dir": direction, "result_of": answered}
        result = message.get("result")
        if answered == "tools/list" and isinstance(result, dict):
            tools = result.get("tools") or []
            record["tools"] = [tool.get("name") for tool in tools if isinstance(tool, dict)]
        if "error" in message:
            record["error"] = True
        self._write(record)


def _pump(source: IO[bytes], sink: IO[bytes], recorder: Recorder, direction: str) -> None:
    for line in iter(source.readline, b""):
        if line.strip():
            recorder.observe(direction, line)
        sink.write(line)
        sink.flush()


def run(argv: list[str], stdin: IO[bytes], stdout: IO[bytes]) -> int:
    log_path, command = parse_args(argv)
    with open(log_path, "a", encoding="utf-8") as log:
        recorder = Recorder(log)
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        child_in, child_out = cast(IO[bytes], child.stdin), cast(IO[bytes], child.stdout)
        server = threading.Thread(
            target=_pump, args=(child_out, stdout, recorder, "server"), daemon=True
        )
        server.start()
        with contextlib.suppress(BrokenPipeError):
            _pump(stdin, child_in, recorder, "client")
        with contextlib.suppress(BrokenPipeError):
            child_in.close()
        code = child.wait()
        server.join(timeout=5)
    return code


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:], sys.stdin.buffer, sys.stdout.buffer))
