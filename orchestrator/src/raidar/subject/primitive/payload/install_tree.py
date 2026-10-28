"""Root-side install of one stage package's tool tree: copy raw bytes, install wheels offline.

Runs as root and never executes authored code: shipped files are checked against their
recorded sha256, copied as data, and dependencies are installed by ``uv`` from hash-pinned
wheels (``--offline --no-index --require-hashes --only-binary :all:``) into a venv made from
the pack's pinned interpreter. Wheel ``.pth`` files and entry points are written as data and
first run as the agent's user. Command wrappers are generated here from quoted argv lists.
"""

from __future__ import annotations

import glob
import hashlib
import os
import re
import shlex
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

COMMAND_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
PATH_ENV = "/usr/local/bin:/usr/bin:/bin"
UV_TIMEOUT_S = 600
PTH_NAME = "raidar-stage-source.pth"


class InstallError(RuntimeError):
    """The install cannot proceed; nothing authored ran."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _uv_env(stage: Path) -> dict[str, str]:
    return {
        "PATH": PATH_ENV,
        "HOME": str(stage / "home"),
        "LANG": "C.UTF-8",
        "UV_NO_CONFIG": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_CACHE_DIR": str(stage / "uv-cache"),
        "UV_NO_PROGRESS": "1",
    }


def _run(argv: list[str], stage: Path) -> None:
    (stage / "home").mkdir(exist_ok=True)
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv from the trusted host, never a shell
            argv, env=_uv_env(stage), cwd="/", capture_output=True, text=True, timeout=UV_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as error:
        raise InstallError(f"{argv[:3]} timed out after {UV_TIMEOUT_S}s") from error
    if done.returncode != 0:
        raise InstallError(f"{argv[:3]} exited {done.returncode}: {done.stderr.strip()[-1500:]}")


def _check_staged(stage: Path, spec: dict[str, Any]) -> None:
    for item in spec["files"]:
        if _sha256(stage / "files" / item["path"]) != item["sha256"]:
            raise InstallError(f"staged file {item['path']} does not match its digest")
    for wheel in spec["wheels"]:
        if _sha256(stage / "wheels" / wheel["file"]) != wheel["sha256"]:
            raise InstallError(f"staged wheel {wheel['file']} does not match its lock")


def _copy_files(stage: Path, root: Path, spec: dict[str, Any]) -> None:
    for item in spec["files"]:
        target = root / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(stage / "files" / item["path"], target)
        target.chmod(0o755 if item["exec"] else 0o644)


def _install_wheels(stage: Path, root: Path, spec: dict[str, Any]) -> None:
    if not spec["venv"]:
        return
    _run([spec["uv"], "venv", "--quiet", "--python", spec["python"], str(root / "venv")], stage)
    if not spec["wheels"]:
        return
    install = [
        spec["uv"], "pip", "install", *spec["flags"],
        "--find-links", str(stage / "wheels"),
        "--link-mode", "copy",
        "--python", str(root / "venv" / "bin" / "python"),
        "-r", str(stage / "requirements.txt"),
    ]  # fmt: skip
    _run(install, stage)


def _write_source_path(root: Path) -> None:
    source = root / "tool" / "src"
    if not source.is_dir():
        return
    sites = glob.glob(str(root / "venv" / "lib" / "python3*" / "site-packages"))
    if len(sites) != 1:
        raise InstallError("the venv has no single site-packages directory")
    (Path(sites[0]) / PTH_NAME).write_text(f"{source}\n", encoding="utf-8")


def _resolve(root: Path, argv0: str) -> str:
    if argv0 in ("python", "python3"):
        return str(root / "venv" / "bin" / "python")
    candidate = root / "venv" / "bin" / argv0
    return str(candidate) if candidate.is_file() else argv0


def _link(wrapper: Path, link: Path, linked: list[Path]) -> None:
    try:
        link.symlink_to(wrapper)
    except FileExistsError as error:
        raise InstallError(f"{link} already exists") from error
    linked.append(link)


def _wrap(root: Path, commands: dict[str, list[str]], links: str) -> list[str]:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    linked: list[Path] = []
    try:
        for name, argv in sorted(commands.items()):
            if not COMMAND_NAME.fullmatch(name) or not argv:
                raise InstallError(f"bad command {name!r}")
            wrapper = bin_dir / name
            resolved = [_resolve(root, argv[0]), *argv[1:]]
            wrapper.write_text(f'#!/bin/sh\nexec {shlex.join(resolved)} "$@"\n', encoding="utf-8")
            wrapper.chmod(0o755)
            _link(wrapper, Path(links) / name, linked)
    except BaseException:
        for link in linked:
            link.unlink(missing_ok=True)
        raise
    return sorted(commands)


def _normalise_modes(root: Path) -> None:
    for directory, _names, files in os.walk(root):
        os.chmod(directory, 0o755)
        for name in files:
            path = os.path.join(directory, name)
            if stat.S_ISREG(os.lstat(path).st_mode):
                os.chmod(path, 0o755 if os.lstat(path).st_mode & 0o111 else 0o644)


def install(spec: dict[str, Any]) -> dict[str, Any]:
    stage, root = Path(spec["stage"]), Path(spec["root"])
    _check_staged(stage, spec)
    root.mkdir(parents=True, mode=0o755)
    _copy_files(stage, root, spec)
    _install_wheels(stage, root, spec)
    _write_source_path(root)
    _normalise_modes(root)
    commands = _wrap(root, spec["commands"], spec["links"])
    shutil.rmtree(stage, ignore_errors=True)
    return {"ok": True, "commands": commands}
