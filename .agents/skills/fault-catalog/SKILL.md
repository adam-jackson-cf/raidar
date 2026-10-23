---
name: "fault-catalog"
description: "Use when extracting, recording, reconciling or targeting conceptual fault categories from Raidar jobs (trials, step results, verifier records, ATIF trajectories, Assessments, analysis.json), or when proposing the next revision of a condition from them."
---

# Task

## Inputs

- The ledger: `ledger/faults/FCR-NNN.md` and `ledger/remedies/M-NNN.md` (see
  [the record schema](references/fault-category-record-schema.md#catalog-location)).
  Read the ledger and every record you may change before changing it. A
  requester may name another location.
- The job(s) to analyse, as `jobs/<group>/<experiment-id>/<job-id>` directories,
  plus their Assessment store and `analysis.json`. Persisted job artifacts are the
  authority; reports and summaries are projections.
- Use [the Raidar evidence map](references/raidar-evidence-map.md) to find each
  artifact and to cite it as `path:line`.
- Load only the runbook the requested action needs.

## Actions

- [Analyse a job](references/analyse-job-runbook.md): extract candidate fault
  categories from verifier records and trajectories, deduplicate them against
  every existing record, and reconcile them.
- [Reconcile evidence](references/update-fault-status-runbook.md): create or
  update records and apply strict status transitions.
- [Select an experiment target](references/select-experiment-target-runbook.md)
  from unresolved records.
- [Propose the next revision](references/propose-next-revision-runbook.md) of a
  condition as component changes, written as a locked, unexecuted experiment.

## Catalog integrity

- Preserve canonical record IDs and wording. After any ledger edit run
  `make ledger-index && make ledger-check` and fix what it reports.
- One record per reusable failure mode, never one per failed verifier case.
- Every occurrence names its job, trial, checkpoint, agent and version, model,
  reasoning effort, arm and condition digest.
- Measurement faults (the verifier, the problem or the harness measures the
  wrong thing) are recorded, but are never targeted by component changes.
- Hidden-test firewall: you may read verifier tests to classify a failure, but a
  proposed component must not carry knowledge that only the hidden tests give.

## Validation

- Every quantitative claim recomputes from persisted artifacts.
- Report changed record IDs, resulting statuses and evidence paths.
