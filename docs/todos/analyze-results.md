# Homepage Matrix Gate Review

Date: 2026-04-20
Scenario: `homepage-implementation@v002`
Harness family reviewed: `codex-cli`

## Scope

I reviewed the latest available homepage experiment artifacts under
`experiments/benchmarks` and classified each observed failure as one of:

- scenario / test / gate failure
- harness / orchestration / configuration failure
- incomplete artifact set that should not be treated as a scored failure

Canonical artifacts used:

- experiment summaries under `experiments/benchmarks/*/experiment-summary.json`
- run records under `experiments/benchmarks/*/runs/*/run.json`
- verifier gate artifacts under `experiments/benchmarks/*/runs/*/verifier/*.json`
- harness logs under `experiments/benchmarks/*/runs/*/harness/*.txt`

## Executive Summary

Not all failures in the reviewed homepage matrix are scenario failures.

Confirmed breakdown:

1. `codex/gpt-5.2-high`, `codex/gpt-5.2-low`, and `codex/gpt-5.2-medium`
   failed because the selected model alias is not supported with the current
   Codex ChatGPT auth mode. These are harness / configuration failures, not
   scenario failures.
2. The targeted rerun for `codex/gpt-5.3-codex-spark-high` completed 5/5 scored
   valid runs. Its failed performance gates are genuine scenario-result
   failures, not orchestration failures.
3. The partially created `codex/gpt-5.3-codex-spark-low`,
   `codex/gpt-5.3-codex-spark-medium`, and
   `codex/gpt-5.3-codex-spark-xhigh` directories are incomplete and should not
   be counted as either scenario failures or successful benchmark rows.

## Experiment Classification

| AgentSpec | Latest artifact | Ranking status | Quality status | Classification |
| --- | --- | --- | --- | --- |
| `codex/gpt-5.2-high` | [summary](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-152835Z__homepage-implementation__v002__codex-cli__codex-gpt-5.2-high/experiment-summary.json>) | `INVALID_FOR_RANKING` | No scored runs | Harness / configuration failure |
| `codex/gpt-5.2-low` | [summary](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-153549Z__homepage-implementation__v002__codex-cli__codex-gpt-5.2-low/experiment-summary.json>) | `INVALID_FOR_RANKING` | No scored runs | Harness / configuration failure |
| `codex/gpt-5.2-medium` | [summary](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-154051Z__homepage-implementation__v002__codex-cli__codex-gpt-5.2-medium/experiment-summary.json>) | `INVALID_FOR_RANKING` | No scored runs | Harness / configuration failure |
| `codex/gpt-5.3-codex-spark-high` | [summary](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-190114Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-high/experiment-summary.json>) | `RANKABLE` | Failed performance gates in all runs | Scenario gate failures |
| `codex/gpt-5.3-codex-spark-low` | no summary | Not rankable | Unknown | Incomplete artifact set |
| `codex/gpt-5.3-codex-spark-medium` | no summary | Not rankable | Unknown | Incomplete artifact set |
| `codex/gpt-5.3-codex-spark-xhigh` | no summary | Not rankable | Unknown | Incomplete artifact set |

## Findings

### F1. `gpt-5.2-*` homepage rows are not scenario failures

All three `gpt-5.2-*` experiments produced 5/5 unscored runs with
`provider_or_harness_turn_failure`.

Evidence:

- [run-01 json, high](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-152835Z__homepage-implementation__v002__codex-cli__codex-gpt-5.2-high/runs/run-01/run.json>)
  `termination_reason = Codex turn failed ... "The 'gpt-5.2-codex' model is not supported when using Codex with a ChatGPT account."`
- [run-01 harness log, high](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-152835Z__homepage-implementation__v002__codex-cli__codex-gpt-5.2-high/runs/run-01/harness/codex.txt>)
  shows the same 400 `invalid_request_error`
- matching evidence exists for:
  - [low run-01](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-153549Z__homepage-implementation__v002__codex-cli__codex-gpt-5.2-low/runs/run-01/run.json>)
  - [medium run-01](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-154051Z__homepage-implementation__v002__codex-cli__codex-gpt-5.2-medium/runs/run-01/run.json>)

Why this matters:

- these rows do not tell us anything about homepage scenario quality
- they should be treated as invalid benchmark rows caused by auth/model
  incompatibility
- the verifier failures under execution validity are downstream artifacts of the
  immediate harness failure, not primary scenario evidence

Supporting gate artifact:

- [execution-validity, high run-01](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-152835Z__homepage-implementation__v002__codex-cli__codex-gpt-5.2-high/runs/run-01/verifier/execution-validity.json>)
  fails `run_completed` because the provider rejected the model before any task
  work began

### F2. The completed `spark-high` rerun is operationally healthy

The targeted rerun for `codex/gpt-5.3-codex-spark-high` completed all 5 repeats
with:

- `run_count_scored = 5`
- `valid_count = 5`
- `validity_rate = 1.0`
- `unscored_count = 0`

Evidence:

- [spark-high experiment summary](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-190114Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-high/experiment-summary.json>)
- [run-01](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-190114Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-high/runs/run-01/run.json>)
- [run-05](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-190114Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-high/runs/run-05/run.json>)

Execution-validity checks passed in every scored run.

Evidence:

- [run-01 execution-validity](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-190114Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-high/runs/run-01/verifier/execution-validity.json>)
- [run-05 execution-validity](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-190114Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-high/runs/run-05/verifier/execution-validity.json>)

Observed execution-validity failure frequency across 5 runs:

- `run_completed`: `0`
- `stack_integrity`: `0`
- `completion_claim_integrity`: `0`
- `required_verification_commands_executed`: `0`
- `commit_verification_hooks_not_bypassed`: `0`
- `atomic_commits_present`: `0`

This confirms the `spark-high` failures are not caused by orchestration breakage.

### F3. The `spark-high` failed performance gates are genuine scenario-result failures

The completed `spark-high` rerun has `performance_pass_count = 0/5`.

Evidence:

- [spark-high experiment summary](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-190114Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-high/experiment-summary.json>)

Failed performance-gate frequencies across 5 scored runs:

- `all_requirements_present`: `5/5`
- `requirement_test_gaps`: `5/5`
- `minimum_quality_score`: `5/5`
- `visual_passed`: `2/5`

Evidence:

- [run-01 performance-gates](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-190114Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-high/runs/run-01/verifier/performance-gates.json>)
- [run-03 performance-gates](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-190114Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-high/runs/run-03/verifier/performance-gates.json>)
- [run-05 performance-gates](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-190114Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-high/runs/run-05/verifier/performance-gates.json>)

These failures are scenario-side because the underlying checks are about
homepage content, structure, semantic evidence, and visual similarity.

Representative evidence:

- `all_requirements_present`:
  `satisfied=2/4, missing=["req-hero-cta","req-features-grid"]`
- `requirement_test_gaps`:
  missing semantic evidence for `navigation`, `link x3`, `heading`, `button`,
  `article x3`, and `contentinfo`
- `minimum_quality_score`:
  quality scores between `0.784` and `0.845`, below the required `0.900`
- `visual_passed`:
  failed in `run-03` and `run-05` due reduced similarity and region pass rate

### F4. Acceptance and requirement failures align with the failed performance gates

The `spark-high` runs repeatedly missed the same authored-copy and structure
expectations.

Requirement gap frequency across 5 runs:

- `req-header-nav`: `5`
- `req-hero-cta`: `5`
- `req-features-grid`: `5`
- `req-footer`: `5`

Representative acceptance failures:

- `Includes hero call-to-action copy`: `5/5`
- `Includes the authored features section heading`: `5/5`
- `Includes the first authored feature card`: `5/5`
- `Includes the second authored feature card`: `5/5`
- `Includes the third authored feature card`: `5/5`
- `Uses the expected section component structure`: `4/5`

Evidence:

- [run-01 acceptance + requirements in run.json](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-190114Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-high/runs/run-01/run.json>)
- [run-04 run.json](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-190114Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-high/runs/run-04/run.json>)

This again points to scenario-output mismatch, not orchestrator malfunction.

### F5. `spark-low`, `spark-medium`, and `spark-xhigh` are incomplete, not failed

These directories exist:

- [spark-low dir](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-185638Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-low>)
- [spark-medium dir](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-190008Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-medium>)
- [spark-xhigh dir](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-190248Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-xhigh>)

But they do not contain:

- `experiment-summary.json`
- `run.json`

So they are incomplete artifacts from the interrupted full matrix and should not
be interpreted as either passed runs or scenario gate failures.

## Process Quality Profile For The Completed `spark-high` Rerun

Source:
[spark-high summary](</Users/adamjackson/Projects/raidar/experiments/benchmarks/20260420-190114Z__homepage-implementation__v002__codex-cli__codex-gpt-5.3-codex-spark-high/experiment-summary.json>)
and its five `run.json` files.

- mean `command_count`: `3.2`
- mean `failed_command_count`: `1.4`
- mean `process_failed_command_count`: `0.0`
- mean `verification_rounds`: `2.0`
- mean `repeated_verification_failures`: `0.6`
- mean `missing_required_verification_commands`: `3.0`
- mean required-verification execution rate: `0.25`
- failed command categories distribution: `{}`

Interpretation:

- the model usually needed at least one local fix-and-rerun loop
- failures happened inside normal verification / commit-hook iteration, not due
  to harness crashes
- the verifier still observed the full required gate set, which is why
  execution-validity passed despite low explicit command coverage

## Conclusion

Confirmed:

1. The `spark-high` performance failures are real scenario-result failures.
   They come from missing authored copy, missing required semantic evidence,
   sub-threshold quality score, and occasional visual-regression failure.
2. The `gpt-5.2-*` rows are not scenario failures. They are model/auth
   incompatibility failures in the Codex ChatGPT harness configuration.
3. The `spark-low`, `spark-medium`, and `spark-xhigh` directories from the
   interrupted full matrix are incomplete artifacts and should not be scored or
   interpreted as gate failures.

So the correct statement is:

- the completed `spark-high` gate failures are scenario failures
- the observed `gpt-5.2-*` failures are orchestration / harness failures
- the remaining partial directories are incomplete and non-diagnostic

## Recommended Next Actions

1. Remove or remap the `gpt-5.2-*` matrix entries when running Codex under
   ChatGPT auth mode.
   Evidence: unsupported-model 400 in the harness logs for all three variants.
2. Do not include incomplete `spark-low`, `spark-medium`, or `spark-xhigh`
   directories in ranking output. Re-run those specs cleanly if comparison is
   still needed.
3. Treat `spark-high` as the current valid scenario baseline and iterate the
   scenario scaffold/prompt against its repeated requirement failures:
   - require the exact authored hero CTA copy
   - require the authored feature section heading and feature card strings
   - enforce the expected `src/components/sections/**/*.tsx` structure
   - require semantic test evidence that satisfies the role-based checks
4. Re-run `spark-high` only after prompt/scaffold updates if the goal is to
   improve benchmark quality rather than just prove orchestration health.
