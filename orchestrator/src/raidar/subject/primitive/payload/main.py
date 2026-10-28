"""Entry point of the trusted payload: ``main.py <command> <json spec path>``.

Every command prints exactly one JSON document on stdout and exits 0 when it produced one.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import Any

from . import install_tree, supervise, walk

MEMINFO = "/proc/meminfo"
CGROUP = "/sys/fs/cgroup"


def _meminfo_available() -> int | None:
    try:
        with open(MEMINFO, encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


def _cgroup_available() -> int | None:
    try:
        with open(f"{CGROUP}/memory.max", encoding="ascii") as handle:
            limit = handle.read().strip()
        with open(f"{CGROUP}/memory.current", encoding="ascii") as handle:
            current = int(handle.read().strip())
        return int(limit) - current if limit.isdigit() else None
    except (OSError, ValueError):
        return None


def probe(spec: dict[str, Any]) -> dict[str, Any]:
    """Free bytes of each path's filesystem and the memory the container can still use."""
    disk: dict[str, int | None] = {}
    for path in spec["paths"]:
        try:
            info = os.statvfs(path)
            disk[path] = info.f_bavail * info.f_frsize
        except OSError:
            disk[path] = None
    memory = [m for m in (_meminfo_available(), _cgroup_available()) if m is not None]
    return {"disk_free": disk, "memory_available": min(memory) if memory else None}


def _manifest(spec: dict[str, Any]) -> dict[str, Any]:
    limits = walk.Limits.from_spec(spec["limits"])
    return walk.scan(spec["path"], limits, entries=spec["entries"])


def _snapshot(spec: dict[str, Any]) -> dict[str, Any]:
    return walk.snapshot(spec["path"], spec["tar"], walk.Limits.from_spec(spec["limits"]))


def _install(spec: dict[str, Any]) -> dict[str, Any]:
    if os.path.lexists(spec["root"]):
        return {"ok": False, "error": f"{spec['root']} already exists"}
    try:
        return install_tree.install(spec)
    except (install_tree.InstallError, OSError) as error:
        shutil.rmtree(spec["root"], ignore_errors=True)
        return {"ok": False, "error": str(error)}


COMMANDS = {"manifest": _manifest, "snapshot": _snapshot, "probe": probe, "install": _install}


def main(arguments: list[str]) -> int:
    if arguments[0] == "supervise":
        return supervise.main(arguments[1:])
    with open(arguments[1], encoding="utf-8") as handle:
        spec = json.load(handle)
    sys.stdout.write(json.dumps(COMMANDS[arguments[0]](spec)) + "\n")
    return 0
