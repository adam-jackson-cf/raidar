"""``gate-check scan``: refuse a workspace that still holds the marker its context names."""

import json
import os
import sys
from pathlib import Path


def scan() -> int:
    context = json.loads(Path(os.environ["RAIDAR_STAGE_CONTEXT"]).read_text(encoding="utf-8"))
    marker = context["params"]["marker"]
    found = [
        str(path)
        for path in sorted(Path().rglob("*.py"))
        if marker in path.read_text(encoding="utf-8")
    ]
    for name in found:
        print(f"{name} still holds {marker}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(scan())
