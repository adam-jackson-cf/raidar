# Analyse Job Runbook

## Objective

Identify distinct candidate fault categories from completed Raidar jobs,
reconcile each through the [Update Fault Status runbook](update-fault-status-runbook.md),
and return entries in the [Reconciliation result schema](reconciliation-result-schema.md).

## Steps

1. Establish identity from persisted artifacts: experiment id and kind, job ids,
   `lock_sha256`, problem pin, agent name and version, model, reasoning effort,
   arms with condition digests, progression mode, scorers and headline metric.
2. Build the cohort ([evidence map](raidar-evidence-map.md#cohort)) and pair arms
   by attempt.
3. Build the per-case failure matrix from verifier records: for every trial and
   checkpoint, each failing case with its `checkpoint_origin`, group, arm and
   attempt. Count, per checkpoint and arm, the cases that are new at that
   checkpoint and the regressions (a case that passed at an earlier step of the
   same trial and fails now).
4. Classify each new failure by mechanism from `grading.log`.
5. Locate the walls: cases that fail in every trial at their origin. For each,
   read the checkpoint's `instruction.md` and decide whether the specification
   determines the asserted value. If it does not, the candidate is a
   `Measurement` fault, not an agent fault.
6. Locate the arm differentials: cases or mechanisms whose per-arm counts
   differ. Test them on paired counts (Fisher or McNemar, stated as
   exploratory) and find where in the step the behaviour was decided: the graded
   snapshot line, the review-loop round diff, the session call.
7. Read the process side from trajectories and component evidence: whether the
   condition's components were consumed (skill opened, hook output visible,
   tools available), what process-compliance observed, and whether the agent's
   environment could run what the guidance asks for.
8. Express each candidate as a reusable failure mode with one concrete example,
   a detection signal, the affected invariant, its impact on the headline
   outcome, and a remediation target (component, verifier, problem, harness or
   analysis).
9. Compare every candidate with every existing record; classify it as an
   occurrence of an existing category or as potentially distinct. Do not
   allocate an ID during analysis.
10. Reconcile every candidate through the Update Fault Status runbook before any
    canonical name is returned.

## Guardrails

- Separate observed facts, inferences and hypotheses, and label each.
- Mark post-hoc analyses (exclusions chosen after seeing the data, sensitivity
  views) as exploratory; never present them as the experiment's verdict.
- Do not produce an experiment-wide summary or recommendations here; that is the
  observations report and the proposal runbook.
