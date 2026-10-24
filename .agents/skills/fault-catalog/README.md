# fault-catalog

## Overview

Maintains a catalog of conceptual fault categories (SlopCodeBench style) observed
in Raidar jobs. It extracts evidence-backed failure modes from verifier records,
ATIF trajectories and Assessments, reconciles them against existing Fault
Category Records, selects controlled targets, and proposes the next revision of
a condition as component changes only.

Ported from `slop-code-bench/.agents/skills/fault-catalog` (SCB) and adapted to
Raidar's model: jobs, trials, step results, attempts, arms and conditions, the
verifier and its rewards, ATIF trajectories, the Assessment store and
`analysis.json`.

## When to use it

- Analyse a completed job (or an A/B pair of arms) for fault categories.
- Reconcile a new occurrence or status against existing records.
- Select the next unresolved fault for a controlled experiment.
- Propose a candidate condition for the next research-loop iteration.

## Example prompts

- "Extract fault categories from var/jobs/pilot-v2/python__forge__guided-pilot/{forge-02,forge-03}."
- "Why is the guided arm worse? Reconcile the evidence with the catalog."
- "Propose the next guided revision as a locked experiment."

## References

- [Raidar evidence map](references/raidar-evidence-map.md)
- [Analyse a job](references/analyse-job-runbook.md)
- [Update fault status](references/update-fault-status-runbook.md)
- [Select an experiment target](references/select-experiment-target-runbook.md)
- [Propose the next revision](references/propose-next-revision-runbook.md)
- [Fault Category Record schema](references/fault-category-record-schema.md)
- [Reconciliation result schema](references/reconciliation-result-schema.md)

## Refinement status (2026-10-02, research-loop iteration 1)

Refined in the port:

- SCB's run vocabulary (run, harness revision, `checkpoint_results`,
  `producer_status.json`, `load_verified_current_generation`) is replaced by
  Raidar's: job, trial record, verifier record, ATIF trajectory, Assessment
  store, `analysis.json`, and the lock as the identity of a configuration.
- Arm-differential evidence: occurrences are counted per arm over paired
  attempts, because a Raidar question is usually "why does arm B differ".
- A `Measurement` category for faults in what is measured (spec-underdetermined
  verifier literals, harness fidelity gaps); such records name an owner outside
  the component space and are never component targets.
- The hidden-test firewall, and a new runbook that turns targeted records into a
  candidate condition written as a locked, unexecuted ab-test.
- Failure-mechanism classes for verifier cases (`detail_exact`,
  `detail_contains`, `cli_surface_missing`, ...) in the evidence map.

Still needs refining:

- **Catalog location.** The tracked home is `lab/ledger/` (`lab/ledger/faults/`,
  `lab/ledger/remedies/`, generated `lab/ledger/README.md`), checked by
  `make ledger-check`. Iteration 1's single-document catalog,
  `docs/reboot/pilot-v2-fault-catalog.md`, predates it and is not migrated
  except for the faults still open, which are seeded in the ledger.
- **Tooling.** The failure classification and the per-arm counts were done with
  ad hoc scripts over `raidar-oracle/steps/*/record.json` and `grading.log`. A
  `make` target that emits a per-case failure matrix (case, origin checkpoint,
  mechanism, arm, attempt) would make the analysis repeatable.
- **Hidden-test firewall checks** are a judgement call recorded in prose; there
  is no mechanical check that a component file is free of verifier literals.
- **Manipulation-check vocabulary.** `process-compliance@1` checks are fixed in
  code (`tests_run_before_finish`, `plan_written`); the runbooks cannot yet
  declare which checks a candidate is expected to move.
- **Status lifecycle across experiments**: a record targeted by an experiment
  that is never run has no expiry rule.
