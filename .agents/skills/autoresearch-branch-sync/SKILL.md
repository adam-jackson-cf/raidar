---
name: "autoresearch-branch-sync"
description: "Use when bringing selected main changes into feat/autoresearch-v3 while preserving autoresearch."
---

# Task

## Select commits

- Prefer cherry-picking wanted commits rather than merging all of main.
- Exclude d1973d6 (refactor: remove autoresearch surface from main).
- Inspect each candidate's dependencies and patch before applying it.
- Skip cleanup that assumes auto_researcher/ is absent, including CI/workflow removal of its setup.
- Skip dependent commits that remove or rewire autoresearch unless reconciliation with removal was explicitly requested.

## Verify and output

- Check the selected changes preserve autoresearch setup and functionality.
- Report applied/skipped commits, reasons and relevant verification.
