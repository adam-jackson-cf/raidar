# Review Surface Design Principles

This document defines the replacement for the current experiment review outputs. The target is a review surface that helps a user decide which agent configuration is stronger on a scenario, why it is stronger, how much to trust that conclusion, and what to try next. It must not behave like a metric dump.

## Scope and Terms

- `agent configuration`: one harness plus one model, for example `codex-cli + codex/gpt-5.4`.
- `Scenario Board`: the high-level view for one scenario, showing one representative experiment for each agent configuration.
- `Experiment Review`: the detailed view for one representative experiment, including runs, evidence, and change context.
- `benchmark`: the pinned comparison configuration for the scenario, for example `claude-code + opus`.
- `representative experiment`: the experiment that stands for a configuration on the board. This is not simply `latest`.

Use `Scenario Board` and `Experiment Review` in the product language. Do not call the overview a leaderboard. `Leaderboard` encourages one-number ranking and hides the actual question, which is comparative task performance.

## Core Design Principles

1. Lead with judgment, not raw numbers.
   Every view should begin with a verdict, a primary strength, a primary weakness, and a next-step hypothesis.

2. Separate absolute status from relative comparison.
   A configuration can be below the benchmark and still meet the scenario bar. It can also be ahead of the benchmark and still have weak reliability. The surface must show both.

3. Treat comparison as hierarchical, not ambiguous.
   Benchmark comparison is primary. Previous representative experiment for the same configuration is secondary. Cohort context is optional and exploratory.

4. Make confidence first-class.
   Verdict language must weaken when the representative experiment is noisy, undersampled, or missing evidence.

5. Scenario fidelity outranks generic efficiency in narrative order.
   For a homepage implementation task, the first question is design replication. Cost and speed matter, but they are supporting context unless the task explicitly prioritizes them.

6. Evidence must sit next to diagnosis.
   If the surface says a configuration is weak on scenario fidelity, the user should be able to inspect the relevant screenshots, diffs, regions, or equivalent scenario-family evidence immediately.

7. Missing signals must be explicit.
   `Unavailable` is a valid state. Missing judge output, missing regional evidence, or insufficient run count must never collapse into an implied pass.

8. Internal metric ids are implementation detail.
   Labels such as `execution-validity`, `artifact-checks`, and `metric_outcomes` should not be the user-facing language of the product.

## Representative Result Rule

The board must not use `latest experiment` as its default unit. It must use a defined representative-result rule.

Recommended rule:

1. Start from the current scenario revision only.
2. Select the latest completed experiment for the configuration that meets the minimum scored-run threshold for that scenario family.
3. If no experiment meets the minimum threshold, show the best available completed experiment as `Low Confidence` instead of pretending it is normal.
4. If only incomplete or unscored experiments exist, show `Unavailable`.

The representative-result rule must be visible in the product so the user knows whether they are looking at a stable result or a weak sample.

## Comparison Model

The surface should support three comparison modes, with clear priority.

- `Benchmark delta`: primary comparison. This answers, `Are we better, worse, or at parity with the pinned benchmark?`
- `Self-trend`: secondary comparison. This answers, `Did the last change improve or regress this configuration?`
- `Cohort standing`: tertiary comparison. This answers, `Where does this configuration sit among the current scenario cohort?`

Do not silently substitute `current best valid configuration` for a benchmark. If no benchmark is pinned, say so explicitly.

## Review Model

The review surface should derive a small set of stable dimensions plus a separate efficiency cluster.

### Canonical Dimensions

| Dimension | Meaning | Current RAIDAR inputs |
| --- | --- | --- |
| `Task Fidelity` | Did the implementation satisfy the authored task and hard acceptance bar? | `functional`, `acceptance`, `requirements_coverage`, `llm-judge` |
| `Scenario Fidelity` | How closely did the output match the scenario-specific target? | `visual` for UI tasks, equivalent scenario-family evidence for non-visual tasks |
| `Workflow Discipline` | Did the agent behave cleanly around required verification and iteration? | required verification commands, first-pass verification success, repeat failures, gate history |
| `Execution Reliability` | Did the run complete cleanly and preserve evaluation validity? | `execution_validity`, termination reason, timeout / early termination signals |
| `Confidence` | How much trust should the product place in the verdict? | scored run count, reruns, unscored rate, cross-run variance, missing evidence |

`Efficiency` should be a separate anchor cluster, not a canonical radar axis. It still matters, but it should not visually compete with fidelity and confidence as if they were equal concepts.

### Absolute Status

Every representative experiment must also carry an absolute status:

- `Meets Scenario Bar`
- `Below Scenario Bar`
- `Unavailable`

Absolute status is not the same as benchmark delta.

## Derivation Rules

The surface needs explicit derivation rules before implementation. The product should not generate verdicts from opaque averaging.

### Task Fidelity Rules

- Deterministic authored failures dominate this dimension.
- Requirement coverage and requirement-to-test mapping cannot contradict deterministic failures without explanation.
- LLM judge findings can enrich diagnosis, but they cannot rescue deterministic failures.
- A configuration that fails a core authored check must not present as broadly strong because softer submetrics averaged well.

### Scenario Fidelity Rules

- This is the canonical fidelity dimension across task families.
- For visual tasks, label the subtype in context as `Visual Fidelity`.
- For non-visual tasks, keep the canonical name but expose the subtype label, for example `Contract Fidelity`.
- Missing scenario-specific evidence lowers confidence rather than creating an artificial neutral score.

### Workflow Discipline Rules

- This dimension is about agent behavior, not task outcome.
- Required verification command execution and first-pass verification success belong here.
- Outcome failures that are really authored-task failures should not be misbucketed as workflow failures.

### Execution Reliability Rules

- This dimension is about single-experiment validity and clean completion.
- `execution_validity`, timeout, termination reason, and stack integrity belong here.
- Cross-run sample instability does not belong here. That belongs in `Confidence`.

### Confidence Rules

- Confidence must be visible, not inferred.
- Inputs should include run count, reruns, unresolved unscored runs, cross-run variation, and missing evidence.
- Verdict language must weaken when confidence is low.
- The board should allow sorting and filtering by confidence directly.

### Efficiency Rules

- Duration and uncached tokens are the primary anchors.
- Command count and failed command count are secondary context, not dominant outcome signals.
- Efficiency should live in a compact anchor cluster, not in the canonical radar.

## Zoom Level 1: Scenario Board

The `Scenario Board` is the fast view for one scenario. It answers:

- Which agent configurations are worth inspecting?
- Which ones meet the scenario bar?
- Which ones are ahead or behind the benchmark?
- How much confidence should we place in each conclusion?

### Recommended Layout

Use a sortable table as the backbone of the board.

Each row should show:

- configuration name
- representative experiment badge
- absolute status
- benchmark delta summary
- confidence
- one-line verdict
- primary strength
- primary weakness
- dimension bars or score cells
- efficiency anchor cluster
- link to the experiment review

Do not force a row-level `opportunity` phrase into the main row if it harms scanability. If present, it should be secondary or revealed on row expand.

### Best Visual Representation

The board should use:

1. A sortable table for exact comparison.
2. Aligned mini-bars or score cells for the five canonical dimensions.
3. A separate benchmark delta strip.
4. A light compare affordance so the user can pick two configurations and open a focused comparison.

Do not use a per-row radar on the board. Tiny radars are hard to compare across rows, overstate shape differences, and imply equal axis importance when the product explicitly does not treat all dimensions equally.

### Board Sort and Filter Priorities

The board should sort and filter well on:

- absolute status
- benchmark delta by dimension
- confidence
- execution reliability
- duration / token anchors

Sorting by generated prose fields is low value.

### Board Metrics

At this zoom level, show:

- `Task Fidelity`
- `Scenario Fidelity`
- `Workflow Discipline`
- `Execution Reliability`
- `Confidence`
- efficiency anchors: duration, uncached tokens, scored run count, unscored count

Do not surface raw requirement ids, gate names, or internal scorecard sections in the board itself.

## Zoom Level 2: Experiment Review

The `Experiment Review` is the diagnostic view for one representative experiment. It answers:

- Why did this experiment win or lose against the benchmark?
- Was the result stable enough to trust?
- What changed from the previous representative experiment?
- What is the next highest-leverage experiment to run?

### Recommended Layout

The view should be organized into six sections.

1. `Outcome Header`
   Show configuration identity, scenario revision, experiment date, run count, absolute status, benchmark delta, and confidence.

2. `Change Context`
   Show what changed from the previous representative experiment so the user can connect interventions to outcomes.

3. `Evidence Strip`
   For visual scenarios, show `reference + current + benchmark + diff` immediately, followed by region cards such as `hero`, `features`, and `footer`.

4. `Diagnosis`
   Show `Strengths`, `Weaknesses`, and `Opportunities`. Each item must be tied to evidence and confidence.

5. `Attribute Comparison`
   Show one radar for current versus benchmark on the five canonical dimensions, paired with exact delta bars.

6. `Run Consistency and Supporting Evidence`
   Show run chips or a run timeline, then expose traces, changed files, gate history, and raw artifacts in drill-down panels.

### Best Visual Representation

At this zoom level, the visual hierarchy should be:

1. Scenario-family evidence
2. Diagnosis text
3. Dimension comparison
4. Run consistency
5. Supporting raw metrics

For homepage-style scenarios, screenshots are the first evidence block. For non-visual scenarios, the evidence strip must swap to the appropriate scenario-family evidence model rather than leaving a visual-shaped hole.

### Region Cards

For visual scenarios, each region card should include:

- region name
- score
- delta versus benchmark or reference
- direct jump or highlight into the corresponding evidence

Without delta and evidence linking, region cards collapse back into another metric tile.

### Detail Metrics by Dimension

`Task Fidelity`

- functional pass status
- acceptance score
- deterministic check failures
- requirement coverage and requirement-to-test mapping
- LLM judge findings when present and trustworthy

`Scenario Fidelity`

- scenario-family subtype label
- global fidelity score
- regional or local fidelity evidence where available
- lowest-scoring area
- diff asset or equivalent evidence

`Workflow Discipline`

- required verification commands executed
- first-pass verification success
- repeat failures
- verification loop behavior

`Execution Reliability`

- execution validity result
- termination reason
- timeout / early termination details
- stack-integrity failures when present

`Confidence`

- scored run count
- reruns used
- unscored run count
- unresolved evidence gaps
- variation across scored runs

`Efficiency`

- uncached input tokens
- duration
- command count
- failed command count

## Language Rules

The language of the surface should read like performance analysis, not eval plumbing.

Verdicts and recommendations should be template-backed and must always communicate:

- comparator
- evidence
- confidence
- proposed lever

Good examples:

- `Behind benchmark on visual fidelity because hero and features regions are weaker; confidence medium; next try prompt and decomposition refinement.`
- `Meets scenario bar and is cheaper than benchmark, but confidence is low because only one scored run completed cleanly.`

Avoid:

- `QUALITY_GAP`
- `INVALID_FOR_RANKING`
- `visual_threshold_met=false`
- `metric_outcomes`
- `artifact-checks`

Every diagnosis item should separate:

- observed fact
- inference
- next-step hypothesis

The recommendation should be framed as a hypothesis, not a certainty.

## Visual Rules

- Use radar charts only in the detailed review, never as the primary board comparison device.
- Use at most five axes.
- Overlay at most two profiles on one radar.
- Pair radar with exact delta bars.
- Use screenshot quartets for visual benchmark comparisons: reference, current, benchmark, diff.
- Use region cards for local diagnosis rather than relying on one global similarity number.
- Use neutral colors for baseline values and saturated colors for meaningful deltas or failures.

## Minimum Data Contract

This surface depends on a stronger derived presentation model than the current summaries provide.

Required:

- representative-experiment selection metadata
- explicit benchmark binding
- absolute status calculation
- confidence calculation inputs
- scenario-family evidence model
- benchmark delta rules
- missing-data handling rules

Required for visual scenarios:

- benchmark output screenshot
- regional scores when regional reference assets exist
- direct links to reference, current, benchmark, and diff assets

Recommended:

- preserve direct links to traces, changed files, gate history, and raw scorecards
- treat `composite_score` as supporting context only
- treat `quality_score` as a derived input, not the diagnosis itself

## Scoring Spec Appendix Requirement

This design is not implementation-ready without a scoring appendix. That appendix must define:

- representative-result rule
- minimum scored-run threshold by scenario family
- absolute-status thresholds
- per-dimension inputs
- hard overrides
- normalization rules
- missing-data behavior
- `ahead / parity / behind` thresholds
- confidence bands and corresponding verdict-language rules

## Implementation Implications

This design implies five product decisions.

First, the board and review need a derived presentation model rather than a direct rendering of raw scorecards.

Second, benchmark semantics must be first-class and visible. Silent fallback to `current best` is not acceptable.

Third, confidence and repeatability must be treated as part of the outcome model, not as buried metadata.

Fourth, the board should optimize for scanability, not compression. That means table-first layout, no per-row radar, and prose that is short enough to remain comparable.

Fifth, the next-experiment recommendation engine must be explicit about evidence and confidence. If the system cannot support a strong recommendation, it should say so.
