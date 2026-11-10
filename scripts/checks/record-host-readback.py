#!/usr/bin/env python3
"""Record the live GitHub default-branch rules and repository merge settings."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

REPOSITORY = "adam-jackson-cf/raidar"
QUERY = (
    "query($owner:String!,$name:String!){repository(owner:$owner,name:$name){"
    "nameWithOwner autoMergeAllowed rebaseMergeAllowed "
    "defaultBranchRef{name target{oid} "
    "branchProtectionRule{requiresStatusChecks requiredStatusCheckContexts}} "
    "rulesets(first:100){nodes{name enforcement conditions{refName{include exclude}} "
    "rules(first:100){nodes{type parameters{"
    "... on RequiredStatusChecksParameters{requiredStatusChecks{context}}}}}}}}}"
)


def main() -> int:
    """Persist exactly the query's status and output, never an invented readback."""
    command = [
        "gh",
        "api",
        "graphql",
        "-f",
        f"query={QUERY}",
        "-F",
        "owner=adam-jackson-cf",
        "-F",
        "name=raidar",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)
    record = {
        "command": command,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "repository": REPOSITORY,
        "query_time": datetime.now(UTC).isoformat(),
    }
    if result.returncode == 0:
        repository = json.loads(result.stdout)["data"]["repository"]
        branch = repository["defaultBranchRef"]
        record.update(default_branch=branch["name"], verified_head=branch["target"]["oid"])
    path = Path(__file__).resolve().parents[2] / ".omp/host-readback.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"host readback: {REPOSITORY}, exit {result.returncode}, {path.name}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
