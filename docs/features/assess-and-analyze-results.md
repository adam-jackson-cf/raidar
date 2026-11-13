# Assess and analyze results

A researcher scores the trials of finished jobs offline, without re-running any agent, then
reads paired effects and verdicts for every arm against the baseline. This follows
[Plan an experiment](plan-an-experiment.md) and a `raidar run`; its output feeds
[Compare experiments](compare-experiments.md).

## Sub-features

- **Rescore.** `raidar assess --rescore <job-dir> --scorer <name@version> --store <store>`
  writes one Assessment per trial and scorer into a store outside the job. End state: an
  Assessment added once is reused unchanged; a provisional one is rerun and superseded under
  the same key once answered.
- **Replay.** `--replay` reruns existing Assessments and refuses any output that differs from
  the stored bytes.
- **Regrade.** `raidar assess --regrade <trial-or-job-dir>` regrades stored snapshots with the
  hidden verifier and fails on any mismatch with the recorded grades.
- **Analyze.** `raidar analyze <job-dir> ... --store <store> --output <dir>` writes
  `analysis.json` and `analysis.md`. End state: each metric reports its paired count, mean
  delta, cost delta, recommended pairs and a verdict (`inconclusive`, `insufficient`, …); a
  missing Assessment is counted as unassessed, never as a zero; the jobs and the store are
  byte-identical afterwards.
- **Lock discipline.** Rescore and analysis read their settings from the experiment lock the
  job ran under, not from the run's execution-time files; a tampered sealed trial refuses
  pooling.

## How to get to it (user POV)

1. Run a locked experiment to get a job under `var/jobs/` (see the
   [experiment workflow](../references/experiment-workflow.md)).
2. `make experiment-assess JOB=<job-dir> STORE=<assessment-store> SCORERS="correctness@10 ..."`.
3. `make experiment-analyze JOBS="<job-dir> ..." STORE=<assessment-store> ANALYSIS_OUT=<dir>`,
   then read `<dir>/analysis.md`.
4. `make assess-regrade JOB=<trial-or-job-dir>` re-checks stored grades when needed.

## Driving it with node scripts/surface/lever.mjs

- This feature crosses no process or UI boundary that the lever serves; outcome tests drive the
  CLI in process over stored jobs, a real Assessment store and real scorers.
- Outcome tests:
  - [test_analysis_command.py](../../orchestrator/tests/test_analysis_command.py): `raidar
    analyze` over rescored jobs, verdicts, lock-following settings, untouched inputs.
  - [test_rescore_store_bindings.py](../../orchestrator/tests/test_rescore_store_bindings.py):
    `raidar assess --rescore` store writes, supersession, replay and the assess gate.
  - [test_analysis_outcomes.py](../../orchestrator/tests/test_analysis_outcomes.py): trial
    outcomes and the rule that a gap is never read as zero.
  - [test_verifier_and_regrade.py](../../orchestrator/tests/test_verifier_and_regrade.py):
    regrade of stored snapshots.
- Command, from the repo root:
  `uv run --project orchestrator pytest orchestrator/tests/test_analysis_command.py
  orchestrator/tests/test_rescore_store_bindings.py orchestrator/tests/test_analysis_outcomes.py
  orchestrator/tests/test_verifier_and_regrade.py`.

## Gotchas

- `--store` and `--output` must lie outside the job directories; the CLI refuses otherwise.
- Verdict overrides (`--min-pairs`, `--alpha`, …) are labelled in the report as post-hoc
  overrides, so a report with them is not the pre-registered result.
- Live scoring with model-backed scorers needs credentials; the outcome tests use recorded
  verifier output and need no Docker, network or credentials.
