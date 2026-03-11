# Review Surface Scoring Spec

This document defines the derivation model for the review surface. It does not replace RAIDAR's underlying scorecard. It defines how the review layer should turn canonical run artifacts into representative experiments, review dimensions, benchmark deltas, confidence bands, and verdict wording.

## Scope

- Inputs come from canonical experiment artifacts: `experiment-summary.json`, `experiment.json`, `runs/*/run.json`, and scenario metadata.
- The review layer may derive new presentation fields, but it must not invent evidence that is not present in the underlying artifacts.
- The review layer must prefer scored runs over aggregate shortcuts when a dimension needs run-level derivation.
- `composite_score` and `quality_score` remain useful background signals, but they must not drive review verdicts directly.

## Canonical Units

- `review identity`: `(scenario_name, scenario_revision, harness, model, evaluation_profile)`.
- `representative experiment`: the single experiment chosen to represent one review identity on the Scenario Board.
- `review run set`: all scored runs inside the representative experiment.
- `benchmark identity`: the pinned review identity for the same scenario and revision. There is no silent fallback to `current best`.
- `evidence anchor run`: the run shown first in detail-view evidence blocks. For visual scenarios, this should be the valid scored run closest to the experiment median on `Scenario Fidelity`.


## Representative Experiment Selection

The Scenario Board must select one representative experiment per review identity using the following algorithm.

1. Filter to completed experiments for the current `scenario_revision`.
2. Exclude experiments with zero scored runs from normal selection. They remain eligible only for `Unavailable` reporting.
3. For each remaining experiment, compute sample adequacy against the scenario-family threshold table below.
4. Prefer the most recent experiment that meets the minimum scored-run threshold.
5. If no experiment meets the threshold, choose the most recent experiment with at least one scored run and mark it `Low Confidence`.
6. If no completed experiment has any scored runs, mark the `AgentSpec` `Unavailable`.

### Minimum Scored-Run Thresholds

| Scenario family | Example | Minimum scored runs | Preferred scored runs for high confidence |
| --- | --- | --- | --- |
| `visual-ui-implementation` | homepage replication | 3 | 5 |
| `code-delivery-nonvisual` | API feature, bugfix, CLI flow | 3 | 5 |
| `open-ended-judged` | planning, migration strategy, loosely judged tasks | 5 | 7 |

The homepage scenario uses the `visual-ui-implementation` threshold.

## Experiment-Level Aggregation Rules

- Dimension scores are derived from the review run set only.
- Use pass rates for binary outcomes and medians for continuous scalar values unless a dimension section says otherwise.
- Hard overrides always win over arithmetic averages.
- Missing optional inputs must reduce confidence or narrow the scoreable surface. They must not be replaced with neutral values.

## Comparator Semantics

Benchmark comparison is primary and must obey the following rules.

1. Compare only against the pinned benchmark for the same `scenario_name`, `scenario_revision`, and compatible `evaluation_profile`.
2. If no compatible benchmark exists, set `benchmark_delta_status` to `Unavailable`.
3. Derive benchmark deltas per canonical dimension and for efficiency anchors separately.
4. Do not collapse benchmark delta and self-trend into a single comparison strip.

### Benchmark Delta Bands

| Delta band | Rule |
| --- | --- |
| `Ahead` | current - benchmark `>= 0.05` on the relevant dimension |
| `Parity` | absolute delta `< 0.05` |
| `Behind` | current - benchmark `<= -0.05` |
| `Inconclusive` | either side has `Confidence < 0.40` or required evidence is missing |
| `Unavailable` | benchmark not pinned or not compatible |

## Canonical Dimensions

The review surface uses five canonical dimensions plus a separate efficiency anchor cluster.

### Task Fidelity

`Task Fidelity` answers whether the output satisfies the authored task and hard acceptance contract.

| Component | Input | Weight |
| --- | --- | --- |
| deterministic acceptance pass rate | authored acceptance checks with `type=deterministic` | 0.45 |
| functional success rate | `functional.passed` and `functional.build_succeeded` | 0.20 |
| requirements presence | median `requirements_coverage.presence_ratio` | 0.15 |
| requirements mapping | median `requirements_coverage.mapping_ratio` | 0.10 |
| LLM-judge pass rate | authored checks with `type=llm_judge` when configured | 0.10 |

Rules:

- If a scenario does not configure `llm-judge`, renormalize the remaining weights.
- Deterministic failures dominate. A deterministic authored check that fails in at least 50% of scored runs caps `Task Fidelity` at `0.49`.
- `llm-judge` may enrich the score when configured, but it must never raise a run above a deterministic failure cap.
- If functional success rate is below `0.50`, cap `Task Fidelity` at `0.39`.

### Scenario Fidelity

`Scenario Fidelity` answers how closely the output matches the scenario-specific target.

For `visual-ui-implementation` scenarios:

| Component | Input | Weight |
| --- | --- | --- |
| global similarity | median `visual.similarity` | 0.60 |
| threshold pass rate | `visual.threshold_met` pass rate | 0.20 |
| regional threshold pass rate | pass rate across region checks from the evidence model | 0.20 |

Rules:

- If regional evidence is unavailable, renormalize to `0.80` global similarity and `0.20` threshold pass rate, and cap `Confidence` at `0.59`.
- If screenshot capture fails in at least 50% of scored runs, cap `Scenario Fidelity` at `0.29`.
- Scenario-family subtypes for non-visual tasks must be defined by the evidence model. Until a subtype contract exists, set `Scenario Fidelity` to `Unavailable` and cap `Confidence` at `0.39`.

### Workflow Discipline

`Workflow Discipline` measures whether the harness behaved cleanly around verification and iteration.

| Component | Input | Weight |
| --- | --- | --- |
| required verification execution rate | required gates/commands actually executed | 0.30 |
| first-pass verification success rate | pass rate with no prior gate failure in the run | 0.30 |
| verification stability | median `verification_stability.score` | 0.25 |
| repeated verification failure penalty | inverse of median `resource_efficiency.repeated_verification_failures` | 0.15 |

Rules:

- If required verification execution rate is below `1.0`, cap `Workflow Discipline` at `0.59`.
- This dimension must not absorb authored task failures. If the task itself failed but verification behavior was clean, the weakness belongs in `Task Fidelity`, not here.

### Execution Reliability

`Execution Reliability` measures whether the experiment completed cleanly and remained valid for comparison.

| Component | Input | Weight |
| --- | --- | --- |
| execution validity pass rate | `execution_validity.passed` | 0.50 |
| early termination penalty | inverse of `terminated_early` rate | 0.20 |
| performance gate pass rate | `performance_gates.passed` | 0.15 |
| crash-free completion rate | runs without fatal `termination_reason` | 0.15 |

Rules:

- If execution validity pass rate is `0.0`, `Execution Reliability` is `0.0`.
- A fatal termination reason in at least 50% of scored runs caps the dimension at `0.39`.
- Cross-run instability does not belong here. It belongs in `Confidence`.

### Confidence

`Confidence` answers how much trust the product should place in the review verdict.

| Component | Input | Weight |
| --- | --- | --- |
| sample adequacy | scored-run count against preferred threshold | 0.35 |
| unresolved unscored burden | inverse of unresolved unscored rate | 0.20 |
| cross-run stability | normalized variance across run-level review dimensions | 0.25 |
| evidence completeness | expected evidence blocks present | 0.20 |

Rules:

- `sample adequacy = min(scored_runs / preferred_threshold, 1.0)`.
- `unresolved unscored burden = 1.0 - (unresolved_unscored_count / total_attempts)`, floored at `0.0`.
- `cross-run stability` should normalize aggregate run-level variance against a `0.15` instability threshold. Experiments above that threshold score `0.0` for this component.
- `evidence completeness` is the fraction of required evidence blocks present for the scenario family.
- Confidence is never inferred from prose. It must be computed and shown directly.

## Efficiency Anchors

Efficiency is shown as supporting context, not as a canonical dimension.

The anchor cluster should include:

- median `duration_sec`
- median `uncached_input_tokens`
- median `resource_efficiency.command_count`
- median `resource_efficiency.failed_command_count`
- median `resource_efficiency.verification_rounds`

The board may display a compact efficiency rank or percentile, but it must not fold efficiency into the five review dimensions.

## Absolute Status Thresholds

Absolute status is separate from benchmark comparison.

### Meets Scenario Bar

Assign `Meets Scenario Bar` only when all of the following are true:

- at least one scored run exists
- no hard override has forced `Task Fidelity` or `Execution Reliability` below the scenario bar
- `Task Fidelity >= 0.85`
- `Scenario Fidelity >= 0.80` for visual scenarios, or the scenario-family threshold for non-visual scenarios
- `Execution Reliability >= 0.85`

### Below Scenario Bar

Assign `Below Scenario Bar` when:

- at least one scored run exists, and
- the representative experiment does not satisfy the `Meets Scenario Bar` rules

### Unavailable

Assign `Unavailable` when:

- no completed experiment has any scored runs, or
- a required scenario-family fidelity contract does not exist yet, or
- evidence is too incomplete to derive the review surface honestly

## Missing-Data Rules

- Missing optional `llm-judge` data does not lower `Task Fidelity` when the scenario does not configure it.
- Missing required evidence lowers `Confidence` and may set a dimension or benchmark delta to `Unavailable`.
- Missing benchmark results never trigger fallback comparison against `current best`.
- If a scenario family cannot yet derive `Scenario Fidelity`, the board must show that explicitly rather than hiding the dimension.
- Unscored runs count against `Confidence`. They do not quietly disappear from the review narrative.

## Confidence Bands

| Band | Score range | Product meaning |
| --- | --- | --- |
| `High` | `>= 0.80` | verdict can use direct, comparative language |
| `Medium` | `0.60 - 0.79` | verdict may state likely conclusions, but should keep some hedge |
| `Low` | `0.40 - 0.59` | verdict should emphasize provisional interpretation |
| `Very Low` | `< 0.40` | verdict should avoid optimization claims and prefer evidence-gathering language |

## Verdict Language Constraints

The review layer must map confidence and comparator status into wording.

| Condition | Allowed language | Disallowed language |
| --- | --- | --- |
| `High` confidence, `Ahead`/`Behind` | `outperforms`, `underperforms`, `wins on`, `lags on` | none |
| `Medium` confidence | `appears stronger`, `looks weaker`, `likely ahead on` | `clearly wins`, `definitively better` |
| `Low` confidence | `may be stronger`, `signal points to`, `provisionally behind` | `is better`, `is worse` |
| `Very Low` confidence or `Inconclusive` delta | `insufficient evidence`, `cannot conclude`, `needs more runs` | any directional claim |

## Required Derived Fields

The scoring layer should emit a derived presentation object per representative experiment containing at least:

- representative experiment id and selection reason
- scored-run count and unresolved unscored count
- absolute status
- five canonical dimension scores
- confidence band
- benchmark delta per dimension
- benchmark delta status
- efficiency anchors
- hard overrides triggered
- evidence completeness summary
