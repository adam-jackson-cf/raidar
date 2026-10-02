# Raidar Evidence Map

## Objective

Name where each kind of evidence lives in a Raidar job, what it is authoritative
for, and how to cite it. Paths are relative to the repository root.

## Job layout

A job is one execution of a locked experiment:
`jobs/<group>/<experiment-id>/<job-id>/`.

| Artifact | Path | Authoritative for |
|---|---|---|
| Job record | `job.json` | job id, experiment, `lock_sha256`, selected trials, `partial`, Docker context |
| Executed lock | `job.lock.json` (and the experiment's `experiment.lock.json` when `lock_sha256` matches) | the configuration identity: problem pin, agent name, version, model, reasoning effort, condition and arm digests, scorers, verdict thresholds |
| Trial record | `trial-records/<trial>.json` | validity checks, `step_results[]` (checkpoint, `checkpoint_state`, `rewards.strict_pass_rate`, `rewards.solved`, `agent_execution_s`, probes), `reached`, phase timings |
| Verifier record | `trials/<trial>/raidar-oracle/steps/<checkpoint>/record.json` | `grade.cases[]`: case `id`, `group` (core, functionality, error, regression), `checkpoint_origin`, `status` |
| Verifier log | `trials/<trial>/raidar-oracle/steps/<checkpoint>/grading.log` | the pytest failure text of each case (assertion line, actual vs expected) |
| Graded snapshot | `trials/<trial>/steps/<checkpoint>/artifacts/app/` | the code the verifier graded |
| ATIF trajectory | `trials/<trial>/steps/<checkpoint>/agent/trajectory.json` | the step's agent trace; review-loop steps reference stage sub-trajectories |
| Agent sessions | `.../agent/sessions/**/*.jsonl` (baseline) or `.../agent/review-loop/stage_<n>_<stage>/sessions/**/*.jsonl` | raw tool calls (`custom_tool_call`) and outputs, line-addressable |
| Component evidence | `.../agent/raidar-components.json`, `.../agent/raidar-components/*` | what each component installed, fired or probed |
| Review loop | `.../agent/review-loop/review-loop.json`, `diffs/round_<n>.patch` | parses, verdicts, feedback, termination reason, per-round diffs |
| Assessment store | `stores/<id>/assessments/<xx>/<digest>.json` (pilot v2: `jobs/pilot-v2/assessment-store`) | scorer observations: correctness, code-quality (V, E, H, R, G), resource-usage, process-compliance (`tests_run_before_finish`, `plan_written`) |
| Analysis | `analysis/<id>/analysis.json` and `analysis.md` (pilot v2: `jobs/pilot-v2/gate-evidence/schema-v2/analysis/forge/`) | effects and verdicts under the lock's thresholds, curves, manipulation check, replaced trials, cost |
| Problem bundle | `problems/store/<source>/<problem>/<pin>/task/steps/<checkpoint>/instruction.md` and `.../oracle/tests/` | the specification the agent saw, and the hidden tests (analyst-only) |

## Cohort

- A trial is in the cohort when every check in `validity.checks` passed and it
  is not listed in `analysis.json` `replaced_trials`.
- Pair arms by attempt index (`b<NN>`); never pool trials from different locks.

## Failure-mechanism classes

Classify each failing case from its `grading.log` block. These classes describe
how the case failed, not why:

| Class | Signal in the failure block |
|---|---|
| `detail_exact` | `assert payload.get("detail") == detail` failed |
| `detail_contains` | `expected detail to contain ...` |
| `cli_surface_missing` | argparse `usage:`, `invalid choice`, `unrecognized arguments` (exit 2, nothing on stdout) |
| `accepted_invalid_input` | `expected error exit code` (the program succeeded) |
| `rejected_valid_input` | `expected success exit code` (the program failed) |
| `wrong_error_category` | `payload.get("error") == category` failed |
| `revision_envelope` | an assertion on the response envelope (problem specific) |
| `value_mismatch` | any other assertion |

A case that fails in every valid trial of every arm at its origin checkpoint is
a candidate `Measurement` fault: read the specification for that checkpoint and
decide whether it determines the asserted value.

## Citing

- Cite files as `path:line` (grading-log line, snapshot source line, session
  JSONL line, instruction line).
- Cite counts with their denominator and arm: "14/16 guided vs 5/16 baseline".
- Treat `result.json`, `report.md` and review-surface projections as displays,
  not as evidence.
