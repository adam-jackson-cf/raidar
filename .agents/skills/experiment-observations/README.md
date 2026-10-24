# experiment-observations

## Overview

Reports what a completed Raidar experiment observed. It validates the jobs'
provenance against their lock, recomputes per-arm outcomes from verifier records
and the Assessment store, reads verdicts from `analysis.json`, reconciles failure
evidence through `fault-catalog`, and writes a traceable observations report. It
gives no recommendations and no model-selection advice.

Ported from `slop-code-bench/.agents/skills/experiment-observations` (SCB) and
adapted to Raidar's jobs, trials, step results, attempts, arms, verifier
rewards, ATIF trajectories, Assessment store and `analysis.json`.

## When to use it

- Report one completed experiment (an ab-test or a benchmark).
- Explain an arm difference with evidence, before proposing a revision.
- Publish reconciled fault evidence for a job.

## Example prompts

- "Report what jobs forge-02 and forge-03 of python__forge__guided-pilot observed."
- "Reconcile the observed faults and give me the concise view."

## References

- [Validate and analyse](references/validate-and-analyze-workflow.md)
- [Reconcile observed faults](references/reconcile-faults-workflow.md)
- [Finalise the report](references/finalize-report-workflow.md)

## Refinement status (2026-10-02, research-loop iteration 1)

Refined in the port:

- Inputs are Raidar jobs and their lock, not an SCB lifecycle handoff payload;
  the scoring authority is the Assessment store plus `make experiment-analyze`
  under the lock's thresholds, not SCB's `load_verified_current_generation`.
- The report is per arm over paired attempts and keeps the analysis verdict
  (`improved`, `worse`, `inconclusive`, `insufficient`) as the only effect
  claim; recomputed numbers are checks, not new verdicts.
- Quality components use Raidar's names (C, V, E, H, R, G, I, S) and the
  headline metric's own definition, read from the lock, instead of SCB's fixed
  table of weights.
- Manipulation-check results are part of the report.

Still needs refining:

- **Report location.** `var/analysis/<experiment-id>/` is gitignored, so reports are
  local unless the user asks for a tracked copy (iteration 1's catalog went to
  `docs/reboot/` with consent).
- **No public target computes the per-case failure matrix**; Step 1 uses ad hoc
  reads of `record.json` and `grading.log`.
- **Benchmark kind.** The template is written for ab-tests; the benchmark
  ranking view (mean ± CI, pass@k, cost) needs its own table.
- **Not exercised end to end.** Iteration 1 used the analysis steps of these
  skills to write a fault catalog; the full observations report and concise
  view have not yet been produced for a job.
