"""Poll a Docker engine and record the Mounts of every Raidar container seen.

Usage: watch_mounts.py <context> <out.jsonl> <stop-file> <arm-out-dir>
Raidar containers: names starting ``raidar-`` or Harbor compose projects ending in the
run scope (``raidar.execution.environment.run_scope``) of a job under ``<arm-out-dir>``.
Anything named ``native-*`` (not ours) is never inspected.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path


def docker(context: str, *args: str) -> str:
    done = subprocess.run(
        ["docker", "--context", context, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout if done.returncode == 0 else ""


def scopes(arm_dir: Path) -> set[str]:
    trials = arm_dir.glob("concurrency-*/jobs/*/perf-fixture/trials")
    return {hashlib.sha256(str(t.resolve()).encode()).hexdigest()[:10] for t in trials}


def main() -> None:
    context, out, stop = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    arm_dir = Path(sys.argv[4])
    seen: set[str] = set()
    out.write_text("")
    while not stop.exists():
        for line in docker(
            context, "ps", "-a", "--format", "{{.ID}}\t{{.Names}}"
        ).splitlines():
            cid, _, name = line.partition("\t")
            if cid in seen or name.startswith("native-"):
                continue
            mine = scopes(arm_dir)
            raw = docker(context, "inspect", cid)
            if not raw:
                continue
            info = json.loads(raw)[0]
            project = info["Config"]["Labels"].get("com.docker.compose.project", "")
            if not (
                name.startswith("raidar-")
                or any(project.endswith(f"-{x}") for x in mine)
            ):
                seen.add(cid)
                continue
            seen.add(cid)
            record = {
                "name": name,
                "project": project,
                "role": info["Config"]["Labels"].get("raidar.role"),
                "mounts": [
                    {k: m.get(k) for k in ("Type", "Source", "Destination", "RW")}
                    for m in info["Mounts"]
                ],
            }
            with out.open("a") as sink:
                sink.write(json.dumps(record) + "\n")
        time.sleep(0.4)


if __name__ == "__main__":
    main()
