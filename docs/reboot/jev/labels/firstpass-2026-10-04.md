# First-pass Jev labels, 2026-10-04: method

`firstpass-2026-10-04.jsonl` holds 68 agent labels: 51 decisions for `code-quality.rubric-smells`
(version `@2` / `scb-rubric@2`) and 17 for `process-compliance.residue` (version `@1` /
`process-residue@1`). Every decision comes from `jobs/pilot-v2/assessment-store`. They are a
first-pass proposal under the evaluate-jev skill's scope rule. Nothing was written to Raidar's
Jev label store, and the user decides whether to adopt them.

- **Labeller:** `by: agent`, `reviewer: model`, `reviewer_model: claude-opus-5-5[1m]`. This is
  the same model family as the existing 38 store labels and as the 2026-10-02 evaluator.
- **Row fields:** each row has the skill's labels format (`id`, `label`, `by`, `note`,
  `timestamp`), plus `case` (the evaluation's C## id where one exists), `set`,
  `state_only_label`, `wider_label` (when wider evidence was read), `literal_stance_label` (for
  stance-sensitive rows; see the note under "Positions applied"), `r1`, the labeller's per-question probabilities (`answers`) and a
  one-line evidence citation. Residue rows also list `same_state_ids`: replays of the same state
  under other trial-id forms.

## Scope and selection

| Set | n | How chosen |
|---|---|---|
| `held_back` | 7 | The evaluation's held-back decisions (C07, C12, C13, C16, C40, C42, C44) |
| `store_false_positive` | 12 | Decisions currently labelled `false_positive` in the store (consensus `clean`, Jev ≥ 0.5) |
| `contested_stance` | 6 | C15, C17 (twins of C07), C25, C28, C39 (stance A) and C33 (stance B) |
| `fresh_random` | 22 | Seeded random draw (seed 20261004) of unlabelled `stage=jev` decisions, stratified by problem, arm and scope. It excludes all 71 ever-labelled ids and the 45 relabel decisions |
| `fresh_stance_b` | 4 | Seeded draw (seed 202610042), two per arm, from unlabelled decisions that ask `silent_resource_creation` of a symbol whose source contains `mkdir` |
| `residue` | 17 | Every distinct asked residue state, deduplicated by state |

## Blindness protocol

- **Blind exports.** All exports used `make jev-export-blind` (state and questions only).
  Labelled decisions are refused by that target, so they were exported from a copy of the store
  with `labels.jsonl` and `label-supersessions.jsonl` removed. The selection scripts read only
  request ids, trials, subjects, questions and source text. Snapshot files were read as wider
  evidence for C19, C37 and C45.
- **Order of work, with timestamps (UTC, 2026-10-04):**
  1. Stances written: 01:42:14.
  2. Labels written: 01:50:33.
  3. Audit records with Jev's answers first opened: 01:51:06.
- **Not blind:** the 25 evaluation cases and the 17 residue states. The labeller read
  `evaluation-2026-10-02.md` first, because the task required it to set the scope and the
  stances. That document shows Jev's maximum noul and the earlier labels for those decisions.
  Treat these labels as informed relabels, not independent ones.
- **Blind:** the 26 fresh decisions (22 random and 4 stance-B). Only these are blind to Jev's
  answers.

## Positions applied

- **Stance A is rejected, with a boundary rule.**
  - A1, clean: comparing argv or argparse subcommand and option tokens to literals at the entry
    point.
  - A2, clean: spec-defined wire values, such as error-category codes, filter tokens like
    `null`, scope values like `system`, and error-type names.
  - A3, smell: raw literals that encode internal state, mode or kind and drive branching in
    domain logic. This includes signal kinds `"input"`/`"wire"` and AST kind `"name"` (C07, C15,
    C17), and a command token turned into a status literal and passed inward (C44).
- **Stance B is rejected for the program's own storage on a write path; kept otherwise.**
  - B1, clean: `mkdir(parents=True, exist_ok=True)` of the data directory inside a save path
    (C33, B2, B3, B4).
  - B2, smell: creation on read or lookup paths, or creation that papers over a missing input.
    No case in this sample hit B2.
  - B1 (the `schema init` case) is clean under both readings, because the criterion's negative
    covers creation in an explicit init function.
- **Residue readings.**
  - P1: every session of the step counts.
  - P2: a suite command that runs no tests does not count. This covers pytest missing and
    unittest discovering 0 tests.
  - P3: a generic "inspect, then implement" preamble is not a plan.
- **Where `literal_stance_label` is set.** It records the label the row would get if stances A
  and B were kept literally. It is set for the multi-branch A1 dispatch rows (C25, C28, C39, C40),
  for C44 and for the stance-B rows (C33, B2, B3, B4). It is left unset for the single argv
  checks C08, C16 and C18, which are also A1 and clean. That is a judgement call: setting it for
  those three rows too would add 3 false positives to the literal view, so its precision of 14/22
  at 0.75 depends on this choice (14/25 otherwise).
- **Validation mismatch:** a missing check that a caller can make is not a mismatch. A
  contradiction with the spec text in the state is a mismatch.

## Disagreements with earlier labels (same model family; reported, not resolved)

- **Versus the 2026-10-02 evaluator:**
  - C07, C15 and C17 are smells here, applying A3 consistently to the twins.
  - C44 is a smell here (A3).
  - C19 is a smell here: `tag update` is detected only before `--data-dir`, and the snapshot
    parser has no `update` subparser.
  - C33 is clean here (B1).
  - Residue `forge-02.b08` cp4 (`jevp_2b4b48b4…`) is `not_complied` here: event [18] says
    unittest discovered no tests.
- **Versus store consensus:**
  - C25, C28 and C39 are clean here; the consensus kept stance A.
  - C33 is clean here (B1).
  - C19 is a smell here.

## Reproduction

The scratchpad scripts and outputs are listed below. The scratchpad is session-local and is not
a durable location.

- Scripts: `work/show.py`, `work/write_labels.py` and `work/export_cases.py`.
- Outputs: `out/*.json`.

```sh
bun ~/.omp/agent/skills/evaluate-jev/scripts/summarize.ts --cases-in out/rubric-cases.jsonl \
  --version "code-quality.rubric-smells@2/scb-rubric@2" --labels-in firstpass-2026-10-04.jsonl --threshold 0.75
bun ~/.omp/agent/skills/evaluate-jev/scripts/summarize.ts --cases-in out/residue-cases.jsonl \
  --version "process-compliance.residue@1/process-residue@1" --labels-in firstpass-2026-10-04.jsonl --threshold 0.5
```
