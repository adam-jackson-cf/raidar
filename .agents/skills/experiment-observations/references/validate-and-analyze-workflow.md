# Validate the jobs and analyse completed trials

1. Accept a set of completed jobs of one experiment
   (`var/jobs/<group>/<experiment-id>/<job-id>`), the Assessment store they were
   assessed into, and the `analysis.json` produced from them. Do not launch,
   resume, stop or clean up jobs; if an input is missing, name the public
   `make` target that produces it (`experiment-assess`, `experiment-analyze`)
   and stop.
2. Establish provenance: every job's `job.json` `lock_sha256` must match the
   lock the analysis used (`analysis.json` `lock_sha256`). Never pool jobs of
   different locks. Record the problem pin, agent name and version, model,
   reasoning effort, arms and condition digests from the lock.
3. Build the cohort from trial records: a trial is included when every
   `validity.checks` entry passed and it is not in `replaced_trials`. Keep the
   ids, paths and reasons of excluded trials. Pair arms by attempt.
4. Treat the Assessment store and `analysis.json` as the scoring authority.
   Report effects and verdicts only as `analysis.json` states them, under the
   thresholds the lock pinned. Treat `report.md`, `result.json` and review-surface
   projections as displays.
5. Recompute, per arm, from verifier records: checkpoints reached, solved share
   per checkpoint (continue and strict views), case pass rate per checkpoint,
   and the count of checkpoints solved. A reached checkpoint is execution
   coverage, not correctness; a checkpoint is solved only when every graded case,
   including regressions, passed. A recomputed value that disagrees with
   `analysis.json` is a finding to report, not a value to substitute.
6. Read quality components (C, V, E, H, R, G) and the headline S from the
   Assessments and the analysis; take their direction and weights from the
   headline metric's definition in the lock (each component in [0, 1], 1 best,
   unless the metric says otherwise). Render a missing or ineligible component as
   `Unavailable` with its reason; never as 0 or 1.
7. Read the manipulation check (per check: shares, p, verdict) and the
   per-arm cost, tokens and agent time. Label `cost_usd` as Harbor's
   token-priced estimate unless billing evidence says otherwise.
8. Build the per-case failure matrix and the mechanism classes described in
   `fault-catalog`'s evidence map, per arm and checkpoint. Keep it as candidate
   evidence; do not name a fault before Step 2. When present, read
   each trial's `<job>/trials/<trial>/health.json` (run-health checks: pass,
   violation or unmeasured; `make health JOB=<job>` recomputes them) and the
   job's `<job>/fault-candidates.jsonl` (provisional
   candidate fault occurrences). Candidates are leads to confirm or reject
   against the artifacts, never findings; a `violation` or `unmeasured` health
   check is reported as such.
9. Prepare the evidence for the report: outcomes, recomputed checks, artifact
   paths, quality components, process evidence, and clearly separated
   interpretations. Record limitations: attempt count, single problem, single
   model and effort, infra exclusions, unavailable components, and any
   exploratory (post-hoc) view.
