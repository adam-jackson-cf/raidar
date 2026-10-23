"""Diff arms of the copy-path spike: ``compare.py A-dir B-dir [C-dir ...]``.

For every concurrency/trial/step it compares the /app snapshot digest, the grading case
verdicts and ledger digest, leak results and reset-report presence against the first arm,
and prints a row per differing field (plus the snapshot digest row for every step).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FIELDS = (
    "snapshot_digest",
    "grading_cases",
    "grading_ledger_digest",
    "leak",
    "pre_agent_leak",
    "has_reset_report",
)


def short(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return text[:12] if len(text) > 12 else text


def main() -> int:
    arms = []
    for arg in sys.argv[1:]:
        path = Path(arg)
        path = path / "summary.json" if path.is_dir() else path
        data = json.loads(path.read_text())
        arms.append((data["arm"], data))
    if len(arms) < 2:
        print(__doc__)
        return 2
    base_name, base = arms[0]
    names = [name for name, _ in arms]
    print(
        f"{'conc':<5} {'trial/step':<40} {'field':<22} "
        + " ".join(f"{n:<14}" for n in names)
    )
    diffs = 0
    for conc, summary in sorted(base["concurrency"].items()):
        for trial, steps in sorted(summary["trials"].items()):
            for step, ref in sorted(steps.items()):
                for field in FIELDS:
                    values = [ref.get(field)]
                    for _, arm in arms[1:]:
                        other = (
                            arm["concurrency"]
                            .get(conc, {})
                            .get("trials", {})
                            .get(trial, {})
                        )
                        values.append(other.get(step, {}).get(field, "MISSING"))
                    same = all(v == values[0] for v in values)
                    diffs += not same
                    if not same or field == "snapshot_digest":
                        mark = " " if same else "!"
                        row = " ".join(f"{short(v):<14}" for v in values)
                        print(
                            f"{conc:<5} {trial + '/' + step:<40} {field:<22} {row} {mark}"
                        )
    for name, arm in arms:
        seen, binds = (
            arm["mounts"]["containers_seen"],
            len(arm["mounts"]["bind_sources"]),
        )
        print(f"{name}: {seen} containers seen, {binds} bind mounts")
    print(f"{diffs} differing field(s) vs {base_name}")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
