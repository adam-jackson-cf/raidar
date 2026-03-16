"""PI RPC subprocess runner with per-role isolated sessions."""

from __future__ import annotations

import json
import selectors
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .models import RoleModelConfig
from .storage import WorkspaceLayout, append_line, ensure_dir, read_text, utc_now_iso, write_text


@dataclass(frozen=True, slots=True)
class RoleExecution:
    """Recorded output for one role invocation."""

    role: str
    session_dir: Path
    request_path: Path
    response_path: Path
    events_path: Path
    assistant_text: str | None


class RoleRunner(Protocol):
    """Role execution interface for PI or test doubles."""

    def validate(self) -> None: ...

    def run_role(
        self,
        *,
        objective_id: str,
        role: str,
        instruction: str,
        model: RoleModelConfig,
    ) -> RoleExecution: ...


@dataclass(slots=True)
class PiRpcRoleRunner:
    """Run PI in RPC mode with one persistent session directory per role."""

    layout: WorkspaceLayout
    pi_binary: str = "pi"
    timeout_sec: int = 900

    def validate(self) -> None:
        if shutil.which(self.pi_binary) is None:
            raise RuntimeError(f"Missing PI binary: {self.pi_binary}")

    def run_role(
        self,
        *,
        objective_id: str,
        role: str,
        instruction: str,
        model: RoleModelConfig,
    ) -> RoleExecution:
        self.validate()
        timestamp = utc_now_iso().replace(":", "").replace("+00:00", "Z")
        session_dir = ensure_dir(self.layout.role_sessions_dir(objective_id, role))
        request_path = self.layout.role_requests_dir(objective_id, role) / f"{timestamp}.md"
        response_path = self.layout.role_responses_dir(objective_id, role) / f"{timestamp}.md"
        events_path = self.layout.role_events_dir(objective_id, role) / f"{timestamp}.jsonl"
        prompt_path = self.layout.role_prompt_path(role)
        if not prompt_path.is_file():
            raise RuntimeError(f"Missing role prompt file: {prompt_path}")
        full_instruction = read_text(prompt_path).rstrip() + "\n\n" + instruction.strip() + "\n"
        write_text(request_path, full_instruction)

        client = _PiRpcClient(
            pi_binary=self.pi_binary,
            cwd=self.layout.repo_root,
            session_dir=session_dir,
            timeout_sec=self.timeout_sec,
        )
        with client:
            client.send("set_model", provider=model.provider, modelId=model.model_id)
            client.send("set_session_name", name=f"{objective_id}:{role}")
            client.prompt_and_wait(full_instruction, events_path=events_path)
            assistant_text = client.get_last_assistant_text()

        write_text(response_path, assistant_text or "")
        return RoleExecution(
            role=role,
            session_dir=session_dir,
            request_path=request_path,
            response_path=response_path,
            events_path=events_path,
            assistant_text=assistant_text,
        )


@dataclass
class _PiRpcClient:
    """Very small JSONL RPC client for PI subprocess sessions."""

    pi_binary: str
    cwd: Path
    session_dir: Path
    timeout_sec: int
    _process: subprocess.Popen[str] | None = field(init=False, default=None)
    _selector: selectors.BaseSelector = field(
        init=False,
        default_factory=selectors.DefaultSelector,
    )
    _request_counter: int = field(init=False, default=0)

    def __enter__(self) -> _PiRpcClient:
        self._process = subprocess.Popen(
            [self.pi_binary, "--mode", "rpc", "--session-dir", str(self.session_dir)],
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if self._process.stdout is None or self._process.stdin is None:
            raise RuntimeError("Failed to initialize PI RPC streams.")
        self._selector.register(self._process.stdout, selectors.EVENT_READ)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            self._shutdown()
        finally:
            self._selector.close()

    def _shutdown(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)
        self._process = None

    def _next_id(self) -> str:
        self._request_counter += 1
        return f"cmd-{self._request_counter}"

    def _read_message(self, *, timeout_sec: float | None = None) -> dict[str, Any]:
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("PI RPC process is not running.")
        deadline = time.monotonic() + (timeout_sec if timeout_sec is not None else self.timeout_sec)
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining == 0.0:
                raise RuntimeError("Timed out waiting for PI RPC output.")
            events = self._selector.select(remaining)
            if not events:
                continue
            line = self._process.stdout.readline()
            if line == "":
                stderr = self._process.stderr.read().strip() if self._process.stderr else ""
            raise RuntimeError(f"PI RPC process closed unexpectedly. {stderr}".strip())
            return json.loads(line)

    def send(self, command_type: str, **payload: Any) -> dict[str, Any]:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("PI RPC process is not running.")
        request_id = self._next_id()
        command = {"id": request_id, "type": command_type, **payload}
        self._process.stdin.write(json.dumps(command) + "\n")
        self._process.stdin.flush()
        while True:
            message = self._read_message()
            if message.get("type") != "response" or message.get("id") != request_id:
                continue
            if not message.get("success", False):
                raise RuntimeError(f"PI RPC command failed: {message}")
            data = message.get("data")
            return data if isinstance(data, dict) else {}

    def prompt_and_wait(self, message: str, *, events_path: Path) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("PI RPC process is not running.")
        request_id = self._next_id()
        self._process.stdin.write(
            json.dumps({"id": request_id, "type": "prompt", "message": message}) + "\n"
        )
        self._process.stdin.flush()
        saw_prompt_response = False
        while True:
            payload = self._read_message()
            append_line(events_path, json.dumps(payload))
            if payload.get("type") == "response" and payload.get("id") == request_id:
                if not payload.get("success", False):
                    raise RuntimeError(f"PI RPC prompt failed: {payload}")
                saw_prompt_response = True
                continue
            if saw_prompt_response and payload.get("type") == "agent_end":
                return

    def get_last_assistant_text(self) -> str | None:
        data = self.send("get_last_assistant_text")
        text = data.get("text")
        return text if isinstance(text, str) else None
