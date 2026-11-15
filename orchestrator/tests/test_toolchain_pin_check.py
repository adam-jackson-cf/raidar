"""Fast toolchain pin exit decisions with in-memory files and subprocess results."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "checks" / "check_toolchain_pins.py"
spec = importlib.util.spec_from_file_location("check_toolchain_pins", SCRIPT)
assert spec is not None
assert spec.loader is not None
checker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checker
spec.loader.exec_module(checker)


@dataclass(frozen=True)
class _PinCase:
    """One repository's pins and toolchain records, and the check's expected verdict."""

    root_pin: bytes | None
    project_pin: bytes | None
    interpreter: str
    declared_uv: str | tuple[str, ...] | None
    locked_uv: str | None
    resolver_uv: str
    exit_code: int
    source: str


@pytest.mark.parametrize(
    "case",
    [
        _PinCase(*pins, "uv==0.10.0", "0.10.0", "uv@0.10.0", code, source)
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
        _PinCase(b"3.13.7\n", b"3.13.7\n", "3.13.7", *versions, code, source)
        for versions, code, source in [
            (
                ("uv==0.10.0", "0.10.0", "uv@0.13.7"),
                1,
                "lab/problems/registry/example/dependencies.json",
            ),
            (
                ("uv==0.10.0", "0.10.0", "pip@25.0"),
                1,
                "lab/problems/registry/example/dependencies.json",
            ),
            (("uv==0.10.0", "0.13.7", "uv@0.10.0"), 1, "orchestrator/uv.lock"),
            ((None, "0.10.0", "uv@0.10.0"), 1, "orchestrator/pyproject.toml"),
            (("uv>=0.10.0", "0.10.0", "uv@0.10.0"), 1, "orchestrator/pyproject.toml"),
            (("uv==0.10.*", "0.10.0", "uv@0.10.0"), 1, "orchestrator/pyproject.toml"),
            (
                (("uv==0.10.0", "uv==0.10.0"), "0.10.0", "uv@0.10.0"),
                1,
                "orchestrator/pyproject.toml",
            ),
            (
                (("uv==0.10.0", "uv>=0.10.0"), "0.10.0", "uv@0.10.0"),
                1,
                "orchestrator/pyproject.toml",
            ),
            (("uv==0.10.0", None, "uv@0.10.0"), 1, "orchestrator/uv.lock"),
            (("uv==0.13.7", "0.13.7", "uv@0.13.7"), 0, ""),
        ]
    ],
)
def test_pin_check_accepts_only_equal_present_pins_and_the_exact_interpreter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: _PinCase
) -> None:
    repo_root = Path("pin-check-repo")
    pins = {
        repo_root / ".python-version": case.root_pin,
        repo_root / "orchestrator" / ".python-version": case.project_pin,
    }
    if case.declared_uv is None:
        declarations: list[str] | tuple[str, ...] = []
    elif isinstance(case.declared_uv, str):
        declarations = [case.declared_uv]
    else:
        declarations = case.declared_uv
    dependencies = ["packaging>=24", *declarations]
    lock = '[[package]]\nname = "packaging"\nversion = "24.0"\n'
    if case.locked_uv is not None:
        lock += f'[[package]]\nname = "uv"\nversion = "{case.locked_uv}"\n'
    record = "lab/problems/registry/example/dependencies.json"
    texts = {
        repo_root / "orchestrator/pyproject.toml": (
            f"[project]\ndependencies = {json.dumps(dependencies)}\n"
            '[tool.uv]\nrequired-version = ">=0.10.0"\n'
        ),
        repo_root / "orchestrator/uv.lock": lock,
        repo_root / record: json.dumps(
            {
                "resolver": f"uv@{case.locked_uv or '0.10.0'}",
                "nested": [{"dependencies": {"resolver": case.resolver_uv}}],
                "other": {"resolver": f"uv@{case.locked_uv or '0.10.0'}"},
            }
        ),
    }

    def read_text(path: Path, **_kwargs: object) -> str:
        return texts[path]

    def read_pin(path: Path) -> bytes:
        pin = pins[path]
        if pin is None:
            raise FileNotFoundError(path)
        return pin

    monkeypatch.setattr(Path, "read_bytes", read_pin)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(checker, "repo_paths", lambda root: [record])
    monkeypatch.setattr(checker.platform, "python_version", lambda: case.interpreter)

    assert checker.main(["--repo-root", str(repo_root)]) == case.exit_code
    captured = capsys.readouterr()
    if case.exit_code:
        assert case.source in captured.err
    else:
        assert "toolchain-pin-check: ok" in captured.out
        assert not captured.err
