"""Harbor environment and secret-file helpers."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

from raidar.harness import (
    SECRET_FILE_ENV_PREFIX,
    CredentialTransport,
    registered_harness_definitions,
)
from raidar.runtime.profile import RuntimeProfile


def _build_harbor_run_env(
    adapter: Any,
    runtime_profile: RuntimeProfile,
) -> dict[str, str]:
    run_env = os.environ.copy()
    run_env.update(adapter.runtime_env())
    _transport_credentials(
        run_env,
        adapter.definition.credentials,
        adapter.resolved_credentials(),
        secret_dir=runtime_profile.secret_transport_dir,
    )
    run_env.update(runtime_profile.compatibility_env)
    return run_env


def _redact_sensitive_text(value: str) -> str:
    assignment_names, value_patterns = _credential_redaction_contract()
    if assignment_names:
        names = "|".join(re.escape(name) for name in assignment_names)
        inline_pattern = re.compile(rf"\b({names})=([^\s\"']+)")
        json_pattern = re.compile(rf'"({names})"\s*:\s*"([^"]+)"')
        value = inline_pattern.sub(r"\1=[REDACTED]", value)
        value = json_pattern.sub(r'"\1":"[REDACTED]"', value)
    for pattern in value_patterns:
        value = re.sub(pattern, "[REDACTED]", value)
    return value


def _credential_redaction_contract() -> tuple[tuple[str, ...], tuple[str, ...]]:
    assignment_names: set[str] = set()
    value_patterns: set[str] = set()
    for definition in registered_harness_definitions():
        for credential in definition.credentials:
            assignment_names.update(credential.redaction.assignment_names)
            value_patterns.update(credential.redaction.value_patterns)
    return tuple(sorted(assignment_names, key=lambda name: (-len(name), name))), tuple(
        sorted(value_patterns)
    )


def _transport_credentials(
    run_env: dict[str, str],
    credential_specs: tuple[Any, ...],
    resolved_credentials: tuple[Any, ...],
    *,
    secret_dir: Path,
) -> None:
    for spec in credential_specs:
        for accepted_name in spec.accepted_names:
            run_env.pop(accepted_name, None)
    for resolved in resolved_credentials:
        spec = resolved.spec
        if spec.transport is CredentialTransport.ENVIRONMENT:
            value = str(resolved.value)
            export_names = spec.export_names or (spec.transport_name,)
            for export_name in export_names:
                run_env[export_name] = value
            continue

        if isinstance(resolved.value, Path):
            secret_path = _write_harbor_secret_file_from_path(
                secret_name=spec.transport_name,
                source_path=resolved.value,
                secret_dir=secret_dir,
            )
        else:
            secret_path = _write_harbor_secret_file(
                secret_name=spec.transport_name,
                secret_value=resolved.value,
                secret_dir=secret_dir,
            )
        run_env[f"{SECRET_FILE_ENV_PREFIX}{spec.transport_name}"] = str(secret_path)


def _write_harbor_secret_file(*, secret_name: str, secret_value: str, secret_dir: Path) -> Path:
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret_path = secret_dir / f"{secret_name.lower()}-{uuid.uuid4().hex}"
    secret_path.write_text(secret_value, encoding="utf-8")
    secret_path.chmod(0o600)
    return secret_path


def _write_harbor_secret_file_from_path(
    *, secret_name: str, source_path: Path, secret_dir: Path
) -> Path:
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret_path = secret_dir / f"{secret_name.lower()}-{uuid.uuid4().hex}"
    secret_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    secret_path.chmod(0o600)
    return secret_path
