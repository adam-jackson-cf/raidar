# Phase 1 spike: codex session resume under Harbor

Date 2026-10-25. Design: [split-design.md](../../../split-design.md) section 5.5, phase 1. Scope: codex only (user decision).

## Method

A throwaway Harbor `BaseAgent` wrapped the native codex agent through the existing `session_chain.create_inner`, and a throwaway launcher swapped it in as the trial runner's agent for `lab/experiments/smoke/python__forge__mechanics` (`MAX_CHECKPOINTS=2`, target `mbp`, codex `0.156.1`, `gpt-6-luna`, reasoning `low`). Per checkpoint it ran:

1. turn 1: `inner.run(task)`, a fresh session;
2. turn 2: a new inner agent's `resume(...)` with "Do not change any file. Reply with one line only: the path of the first file you created or edited earlier in this session, or NONE if this session has no earlier turn."

Between turns `/logs/agent/sessions` was left in place (copied to the host, not moved). Job: `var/jobs/python__forge__mechanics/spike-resume-03` (jobs `-01` and `-02` ran without the wrapper because the make override missed `RAIDAR_SECRETS`; they are not evidence).

## Result

| Turn | Session id | Reply | Harbor context tokens (input / cached / output) | Rollout total after the turn |
|---|---|---|---|---|
| cp1 turn 1 | `01a1755b-…3f10` | implemented `forge.py` | 61,525 / 55,424 / 2,312 | same |
| cp1 turn 2 (resume) | `01a1755b-…3f10` | `/app/forge.py` | 78,712 / 72,320 / 2,320 | same |
| cp2 turn 1 | `01a1755c-…651e` | implemented allocations in `forge.py` | 61,481 / 55,296 / 1,547 | same |
| cp2 turn 2 (resume) | `01a1755c-…651e` | `/app/forge.py` | 78,841 / 72,320 / 1,555 | same |

- **Resume works under Harbor in Raidar's copy-mode sandbox.** `codex exec resume --last` continued the same session id and appended to the same rollout file; the resumed turn answered from the earlier turn's history (about 4 s).
- **The next checkpoint starts fresh**: checkpoint 2 got a new session id.
- **Harbor's context after a resumed turn is the session's cumulative total**, so summing per-turn contexts double-counts (review finding confirmed). Per-turn usage is the difference between consecutive totals (cp1 turn 2: 17,187 input, 16,896 cached, 8 output); the deltas add up to the session total by construction.

## What phase 2 takes from this

- The workflow runner keeps the active session store (`/logs/agent/sessions`) in place between turns of one checkpoint and copies, never moves, it into each turn's log directory.
- Fix and rework turns call the native agent's `resume`; every new checkpoint (and the define stage) calls `run`.
- Usage is recorded per turn as the delta from the previous turn of the same session, with the session total kept for reconciliation.
