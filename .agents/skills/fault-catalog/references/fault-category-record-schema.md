# Fault Category Record Schema

## Objective

Define the ledger's record shapes, the statuses and the integrity rules every
action uses.

## Catalog location

- The catalogue is the tracked `lab/ledger/` directory: `lab/ledger/faults/FCR-NNN.md`
  (fault categories), `lab/ledger/remedies/M-NNN.md` (remedies) and a generated
  `lab/ledger/README.md` index. One file per record; never reuse an identifier.
- After every edit run `make ledger-index && make ledger-check`. The check fails
  on unknown keys, missing fields, id/filename mismatch, broken remedy→fault
  links, illegal status transitions, backwards dates and a stale index. Never
  edit `lab/ledger/README.md` by hand.
- A requester may name another location; otherwise use `lab/ledger/`.

## Fault record

Markdown with YAML frontmatter (unknown keys are refused); free prose goes in the body.

```markdown
---
id: FCR-NNN
title: Canonical fault category
category: Behavioral | Integration | Recovery | Structural | Process | Measurement
owner: raidar:<area> | subject:<repo>
status: open
evidence: [var/jobs/<group>/<experiment>/<job>/...:line]   # relative paths, `:line[-line]` optional
configurations: ["experiment/job, arm (condition digest), checkpoints, agent+version, model, effort, measured result"]
impact: consequence for the headline outcome and cumulative work
hypothesis: suspected cause, labelled [HYPOTHESIS]
criterion: measurable condition set before targeting, or null
target: experiment id or null
solution: intervention or null
validation: artifact and measurement or null
status_history:
  - {status: open, date: YYYY-MM-DD, evidence: [path], reason: why}
---
Description and detection signal (an observable pattern later jobs can evaluate).
```

`status` equals the last `status_history` status; the first is `open`. Append one
history entry per status change (status, date, evidence refs, reason); never
rewrite earlier entries. `var/jobs/...` refs need not exist (jobs are machine-local)
but must be well-formed relative paths. An observed configuration proves that
configuration showed the fault; its absence does not prove another is immune.
Mark anything uncertain in the record.

## Remedy record (M)

```markdown
---
id: M-NNN
title: Remedy
faults: [FCR-NNN]
lever: free string
placement: where the change goes
home: raidar | subject:<repo>@<version>
rationale: why it should move the fault's signal
proof_plan: experiment that would decide it
experiment: lab/experiments/.../experiment.yaml or the lock path, or null
status: proposed
status_history: [{status: proposed, date: YYYY-MM-DD, evidence: [], reason: why}]
outcome: result or null
---
```

Remedy status: `proposed → locked → executed → measured → adopted`; `rejected`
from any non-terminal status. Link a fault to its remedy by `faults`; move the
fault to `targeted` only when a locked experiment names it.

## Categories

| Category | Meaning |
|---|---|
| Behavioral | the agent's program does the wrong thing |
| Integration | the program fails at a boundary (CLI contract, dependencies, environment) |
| Recovery | the agent fails to repair a failure it could observe |
| Structural | the change damages earlier behaviour or code structure (regressions, erosion) |
| Process | the agent's process, or the condition's delivery of it, deviates from what the condition intends |
| Measurement | the verifier, the problem or the harness measures something other than the intended construct; the owner is outside the component space |

## Status lifecycle

| Status | Meaning |
|---|---|
| `open` | Observed and evidenced; no intervention selected. |
| `targeted` | A locked experiment explicitly targets the fault. |
| `mitigated` | An intervention improved the signal without meeting the full criterion. |
| `resolved` | The pre-registered criterion passed and the solution is recorded. |
| `reopened` | A later job reproduced a resolved fault. |
| `not-reproducible` | A controlled follow-up could not reproduce it. |
| `routed` | A Measurement fault handed to its owner (verifier, problem, harness, analysis). |

```text
open → targeted → mitigated → resolved
            └──────────────→ resolved
resolved → reopened → targeted
open → not-reproducible
open → routed
```

A lower failure count alone is not resolution. `ledger check` enforces exactly these edges.

## Integrity rules

- Preserve IDs and canonical wording; allocate IDs sequentially; never renumber.
- Never delete records, history, unsuccessful interventions or resolved records.
- Keep procedure in this skill, not in the ledger.
- Keep every number traceable to persisted job artifacts.
- Never include credentials or secret values.
- Distinguish nominal token-priced cost from billed cost.
