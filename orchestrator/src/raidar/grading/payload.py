"""The explicit inventory of bytes shipped into the hidden grading sandbox."""

from __future__ import annotations

from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parent / "runtime"
RESOURCES = frozenset({"coverage.rc", "pytest.ini", "raidar_oracle_plugin.py", "sitecustomize.py"})
# These modules are loaded by the sandbox interpreter/pytest, not by host imports.
SUBPROCESS_ROOTS = (
    "raidar.grading.runtime.raidar_oracle_plugin",
    "raidar.grading.runtime.sitecustomize",
)
PLUGIN_ROOTS = ("pytest", "coverage")
DYNAMIC_IMPORTS = {"raidar.grading.runtime.raidar_oracle_plugin": frozenset({"coverage"})}


def runtime_files(root: Path = RUNTIME_DIR) -> dict[str, Path]:
    """Exactly the regular payload files that are hashed and tarred; fail closed on drift."""
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts:
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"unaccounted grading resource: {relative}")
        files[relative.as_posix()] = path
    if set(files) != RESOURCES:
        raise ValueError(
            f"grading resource inventory mismatch: missing={sorted(RESOURCES - files.keys())}, "
            f"unaccounted={sorted(files.keys() - RESOURCES)}"
        )
    return files
