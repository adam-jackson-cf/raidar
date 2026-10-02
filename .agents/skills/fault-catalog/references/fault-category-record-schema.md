# Fault Category Record Schema

## Objective

Define the catalog index, the Fault Category Record shape, the statuses and the
integrity rules every action uses.

## Catalog location

- The requester names the catalog document. Raidar has no default tracked home
  yet (`analysis/` is gitignored; `docs/` needs user consent). Do not create a
  parallel catalog when one exists.
- Single-document form (the default): an index table followed by one
  `## FCR-NNN — <fault category>` section per record.
- Directory form (when the requester asks): `<root>/fault-catalog.md` plus
  `<root>/fault-category-records/FCR-NNN-<slug>.md`.
- Never reuse an identifier.

## Index

```markdown
| ID | Fault category | Category | Owner | Status | First observed | Current target |
|---|---|---|---|---|---|---|
| [FCR-NNN](#fcr-nnn) | Canonical fault category | Category | component / verifier / problem / harness / analysis | `status` | experiment / job | experiment or — |
```

## Record

```markdown
## FCR-NNN — Canonical fault category

- **Category:** Behavioral | Integration | Recovery | Structural | Process | Measurement
- **Owner:** component | verifier | problem | harness | analysis
- **Status:** `open`
- **Description:** the recurring failure mode.
- **Detection signal:** an observable pattern in verifier records, trajectories or Assessments.
- **Arm differential:** per-arm occurrence over paired attempts, or "none".
- **Example evidence:** one trial, checkpoint and measurement, cited as `path:line`.
- **Observed configurations:**

  | Experiment / job | Arm (condition digest) | Checkpoints | Agent + version | Model | Effort | Evidence |
  |---|---|---|---|---|---|---|

- **Impact:** the consequence for the headline outcome and for cumulative work.
- **Working hypothesis:** the suspected cause, labelled as a hypothesis.
- **Resolution criterion:** a measurable condition on the broader behaviour, set before targeting.
- **Target experiment:** experiment id or `—`.
- **Solution:** intervention or `—`.
- **Validation evidence:** artifact and measurement or `—`.
- **History:**
  - YYYY-MM-DD — event, experiment and job.
```

An observed-configuration row proves that configuration showed the fault; its
absence does not prove another configuration is immune.

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

A lower failure count alone is not resolution.

## Integrity rules

- Preserve IDs and canonical wording; allocate IDs sequentially; never renumber.
- Keep the index and records synchronised in the same change.
- Never delete records, history, unsuccessful interventions or resolved records.
- Keep procedure in this skill, not in the catalog.
- Keep every number traceable to persisted job artifacts.
- Never include credentials or secret values.
- Distinguish nominal token-priced cost from billed cost.
