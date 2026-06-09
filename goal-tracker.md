# Tracker Guidance

Update this tracker when material events occur. Keep entries concise and evidence-oriented. Prefer high-risk, high-complexity, user-impacting, or direction-changing events over routine progress.

This tracker supports `goal.md`; it does not replace it.

# Current Status

Not started.

# Goal Summary

Deep-dive and complete Raidar's scorer platform by promoting or retiring proposed scorers, hardening scorer runtime/schema/reporting mechanics, creating grouped demonstration scenarios for the completed scorer set, proving every new or revised scenario with `gpt-5.5` low reasoning plus deterministic/unit verification, and finishing with passing quality gates and atomic commits.

# Chronological Progress Log

- Pending: Start scorer-system audit.

# Git Commits Made

- None yet.

# Implementation Runtime Design Decisions

- Pending.

# Direction Changes, Overwritten Code, Removed Code, Or Substantial Refactors

- Pending.

# Verification Evidence And Quality Gates

- Pending.

# Remaining Open Questions Discovered During Implementation

- Pending.

# Key Implementation Map

- Scorer implementations: `orchestrator/src/raidar/scorers/`
- Scorer runtime assembly: `orchestrator/src/raidar/runtime/scoring_outputs.py`
- Scorecard aggregation: `orchestrator/src/raidar/runtime/scorecard.py`
- Scenario schema validation: `orchestrator/src/raidar/schemas/scenario.py`
- Scenario roots: `scenarios/`
- Quality entrypoint: `make quality`

# Deferred Work And Explicitly Rejected Paths

- Pending.
