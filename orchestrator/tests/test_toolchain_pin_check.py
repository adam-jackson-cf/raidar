"""Fast toolchain pin exit decisions with in-memory files and subprocess results."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "checks" / "check_toolchain_pins.py"
spec = importlib.util.spec_from_file_location("check_toolchain_pins", SCRIPT)
assert spec is not None
assert spec.loader is not None
checker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checker
spec.loader.exec_module(checker)


@pytest.mark.parametrize(
    (
        "root_pin",
        "project_pin",
        "interpreter",
        "required_uv",
        "host_uv",
        "ci_uv",
        "resolver_uv",
        "exit_code",
        "source",
    ),
    [
        (*pins, "0.10.0", "0.10.0", "0.10.0", "0.10.0", code, source)
        for pins, code, source in [
            ((b"3.12.9\n", b"3.12.9\n", "3.12.9"), 0, ""),
            ((b"3.13.7\n", b"3.13.7\n", "3.13.7"), 0, ""),
            ((b"3.13.7\n", b"3.13.7\n", "3.14.10"), 1, "running Python"),
            ((b"3.13.7\n", b"3.13.8\n", "3.13.7"), 1, "not byte-equal"),
            ((b"3.13.7\n", b"3.13.7", "3.13.7"), 1, "not byte-equal"),
            ((b"3.13.7\n", b"3.13.7\r\n", "3.13.7"), 1, "not byte-equal"),
            ((None, b"3.13.7\n", "3.13.7"), 1, "missing .python-version"),
            ((b"3.13.7\n", None, "3.13.7"), 1, "missing orchestrator/.python-version"),
            ((None, None, "3.13.7"), 1, "missing .python-version"),
            ((None, b"3.13.7\n", "3.14.10"), 1, "running Python"),
        ]
    ]
    + [
        (b"3.13.7\n", b"3.13.7\n", "3.13.7", *versions, code, source)
        for versions, code, source in [
            (("0.10.0", "0.13.7", "0.10.0", "0.10.0"), 1, "host uv"),
            (
                ("0.10.0", "0.10.0", "0.13.7", "0.10.0"),
                1,
                ".github/workflows/second.yaml",
            ),
            (
                ("0.10.0", "0.10.0", None, "0.10.0"),
                1,
                ".github/workflows/second.yaml",
            ),
            (
                ("0.10.0", "0.10.0", "0.10.0", "0.13.7"),
                1,
                "lab/problems/registry/example/dependencies.json",
            ),
            (("0.13.7", "0.13.7", "0.13.7", "0.13.7"), 0, ""),
        ]
    ],
)
def test_pin_check_accepts_only_equal_present_pins_and_the_exact_interpreter(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    root_pin: bytes | None,
    project_pin: bytes | None,
    interpreter: str,
    required_uv: str,
    host_uv: str,
    ci_uv: str | None,
    resolver_uv: str,
    exit_code: int,
    source: str,
) -> None:
    repo_root = Path("pin-check-repo")
    pins = {
        repo_root / ".python-version": root_pin,
        repo_root / "orchestrator" / ".python-version": project_pin,
    }
    workflow = (
        "jobs:\n  check:\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: astral-sh/setup-uv@v7\n        with:\n"
        f'          version: "{required_uv}"\n'
    )
    later_step = "      - uses: astral-sh/setup-uv@v7\n" + (
        f'        with:\n          version: "{ci_uv}"\n' if ci_uv else ""
    )
    record = "lab/problems/registry/example/dependencies.json"
    texts = {
        repo_root / "orchestrator/pyproject.toml": (
            f'[tool.uv]\nrequired-version = "=={required_uv}"\n'
        ),
        repo_root / ".github/workflows/first.yml": workflow,
        repo_root / ".github/workflows/second.yaml": workflow + later_step,
        repo_root / record: json.dumps(
            {
                "resolver": f"uv@{required_uv}",
                "nested": [{"dependencies": {"resolver": f"uv@{resolver_uv}"}}],
                "other": {"resolver": "pip@25.0"},
            }
        ),
        repo_root / "lab/problems/registry/untracked.json": '{"resolver":"uv@99.0.0"}',
    }

    def read_text(path: Path, **_kwargs: object) -> str:
        return texts[path]

    def glob(path: Path, pattern: str) -> list[Path]:
        assert path == repo_root / ".github/workflows"
        return [path / ("first.yml" if pattern == "*.yml" else "second.yaml")]

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "uv":
            return subprocess.CompletedProcess(command, 0, f"uv {host_uv} (build date)\n")
        assert command[0] == "git"
        return subprocess.CompletedProcess(command, 0, record + "\0")

    def read_pin(path: Path) -> bytes:
        pin = pins[path]
        if pin is None:
            raise FileNotFoundError(path)
        return pin

    monkeypatch.setattr(Path, "read_bytes", read_pin)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "glob", glob)
    monkeypatch.setattr(checker.subprocess, "run", run)
    monkeypatch.setattr(checker.platform, "python_version", lambda: interpreter)

    assert checker.main(["--repo-root", str(repo_root)]) == exit_code
    captured = capsys.readouterr()
    if exit_code:
        assert source in captured.err
        assert f"uv self update {required_uv}" in captured.err
    else:
        assert "toolchain-pin-check: ok" in captured.out
        assert not captured.err
