"""Harbor environment and secret-file helpers."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

from raidar.runtime.profile import RuntimeProfile, default_runtime_profile

INLINE_SECRET_PATTERN = re.compile(
    r"\b("
    r"OPENAI_API_KEY|ANTHROPIC_API_KEY|CLAUDE_CODE_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|"
    r"GEMINI_API_KEY|COPILOT_API_KEY|CURSOR_API_KEY|PI_API_KEY|"
    r"GOOGLE_APPLICATION_CREDENTIALS"
    r")=([^\s\"']+)"
)

JSON_SECRET_PATTERN = re.compile(
    r'"('
    r"OPENAI_API_KEY|ANTHROPIC_API_KEY|CLAUDE_CODE_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|"
    r"GEMINI_API_KEY|COPILOT_API_KEY|CURSOR_API_KEY|PI_API_KEY|"
    r"GOOGLE_APPLICATION_CREDENTIALS"
    r')"\s*:\s*"([^"]+)"'
)

KEY_LIKE_TOKEN_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")

SECRET_ENV_KEYS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GEMINI_API_KEY",
)

SECRET_FILE_ENV_PREFIX = "AGENTIC_EVAL_SECRET_FILE_"


def _build_harbor_run_env(
    adapter: Any,
    runtime_profile: RuntimeProfile | None = None,
) -> dict[str, str]:
    profile = runtime_profile or default_runtime_profile()
    run_env = os.environ.copy()
    run_env.update(adapter.runtime_env())
    for key in adapter.excluded_run_env_keys():
        run_env.pop(key, None)
    if adapter.harbor_harness_import_path():
        _inject_secret_file_env(run_env)
        _inject_local_secret_file_env(run_env, adapter.local_secret_files())
    run_env.update(profile.compatibility_env)
    return run_env


def _redact_sensitive_text(value: str) -> str:
    redacted = INLINE_SECRET_PATTERN.sub(r"\1=[REDACTED]", value)
    redacted = JSON_SECRET_PATTERN.sub(r'"\1":"[REDACTED]"', redacted)
    return KEY_LIKE_TOKEN_PATTERN.sub("[REDACTED]", redacted)


def _inject_secret_file_env(run_env: dict[str, str]) -> None:
    for key in SECRET_ENV_KEYS:
        secret_value = run_env.pop(key, "")
        if not secret_value:
            continue
        run_env[f"{SECRET_FILE_ENV_PREFIX}{key}"] = str(
            _write_harbor_secret_file(secret_name=key, secret_value=secret_value)
        )


def _inject_local_secret_file_env(run_env: dict[str, str], secret_files: dict[str, Path]) -> None:
    for key, source_path in secret_files.items():
        run_env[f"{SECRET_FILE_ENV_PREFIX}{key}"] = str(
            _write_harbor_secret_file_from_path(secret_name=key, source_path=source_path)
        )


def _write_harbor_secret_file(*, secret_name: str, secret_value: str) -> Path:
    secret_dir = Path.home() / ".agentic-eval" / "secrets"
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret_path = secret_dir / f"{secret_name.lower()}-{uuid.uuid4().hex}"
    secret_path.write_text(secret_value, encoding="utf-8")
    secret_path.chmod(0o600)
    return secret_path


def _write_harbor_secret_file_from_path(*, secret_name: str, source_path: Path) -> Path:
    secret_dir = Path.home() / ".agentic-eval" / "secrets"
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret_path = secret_dir / f"{secret_name.lower()}-{uuid.uuid4().hex}"
    secret_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    secret_path.chmod(0o600)
    return secret_path
