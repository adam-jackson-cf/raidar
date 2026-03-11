# Oracle Review: Review Surface Design Principles

- Date: `2026-03-11`
- Reviewer: `Oracle browser mode`
- Model: `gpt-5.2-pro`
- Target: [review-surface-design-principles.md](/Users/adamjackson/Projects/raidar/docs/todos/review-surface/review-surface-design-principles.md)
- Source artifacts:
  - [.enaible/artifacts/oracle/raidar-review-surface-answer.md](/Users/adamjackson/Projects/raidar/.enaible/artifacts/oracle/raidar-review-surface-answer.md)
  - [.enaible/artifacts/oracle/raidar-review-surface-session.txt](/Users/adamjackson/Projects/raidar/.enaible/artifacts/oracle/raidar-review-surface-session.txt)

## Overall verdict

This is directionally sound, and the `Scenario Board` / `Experiment Review` split is the right replacement for the current metric-dump output.

The serious issue is that the doc is much stronger on framing than on the derivation model underneath it. The success of this surface will not come from layout alone. It will come from whether you can define, consistently and transparently:

- what counts as the representative experiment for a configuration
- which comparator owns the narrative
- how the five dimensions are scored
- how confidence and missing data affect verdicts
- how a `next experiment` recommendation is generated

The core product direction is good, but the document was not implementation-ready in its original form.

## Strengths

- It solves the right problem: missing judgment, comparison, and actionability rather than missing raw metrics.
- The two zoom levels map well to user questions.
- Rejecting leaderboard language is correct.
- Comparison as the default mode is the right instinct.
- The visual-task hierarchy is strong for homepage-style scenarios.
- The table-first board is the right scalable structure.
- Explicit `Unavailable` states are important and correctly called out.

## Weaknesses

- `Latest experiment per configuration` is too weak a unit.
- The comparison model was muddled across benchmark, self-improvement, and cohort standing.
- Falling back to `current best valid configuration` is risky and unstable.
- The five-dimension model needed explicit scoring, overrides, missing-data rules, and thresholds.
- Consistency mattered, but was not first-class.
- The taxonomy mixed outcome, process, and confidence.
- The recommendation model was too loose.
- The board risked becoming overcompressed.
- The design still leaned heavily toward visual tasks.
- The language rules removed jargon but not overclaiming.

## Visual representation review

- Radar is acceptable as a secondary aid in `Experiment Review`.
- Radar is weak on the `Scenario Board`.
- The board should prefer aligned mini-bars or score cells over row-level radar.
- Screenshots and diffs should lead for homepage-style scenarios.
- Benchmark output must appear beside the current output, not just reference and diff.
- Region cards only work if they include delta and a direct tie to evidence.
- Benchmark delta and self-trend should not share one ambiguous comparison strip.

## Metric framework review

- The five-dimension compression was a good starting shape, but the mapping needed to be cleaner.
- `Task Fidelity` needed deterministic failures to dominate.
- `Design Fidelity` was better expressed as a canonical `Scenario Fidelity` dimension with subtype labels in context.
- `Verification Discipline` needed a cleaner behavioral definition.
- `Execution Reliability` needed to stay about single-experiment validity, not cross-run stability.
- `Efficiency` should not visually compete as an equal radar axis.
- The design needed a separate concept for absolute scenario bar versus benchmark-relative gap.
- Confidence needed to become a top-level model input and user-visible output.

## Information architecture review

- The `Scenario Board` / `Experiment Review` split is correct.
- The board should use a representative result, not merely the latest result.
- Benchmark should be the primary comparator.
- The board should expose absolute status and confidence before prose density.
- The detailed review needed more experiment context in the header.
- A lightweight side-by-side compare affordance was missing.
- Non-visual scenarios needed a proper evidence template.

## Concrete changes requested by Oracle

1. Replace `latest experiment` with a representative-result rule.
2. Make comparator semantics explicit.
3. Add a scoring-spec appendix.
4. Promote repeatability/confidence to a first-class concept and clean the taxonomy.
5. Change board/detail comparison visuals.
6. Constrain generated language and recommendation logic around comparator, evidence, confidence, and proposed lever.

## Outcome

The current [review-surface-design-principles.md](/Users/adamjackson/Projects/raidar/docs/todos/review-surface/review-surface-design-principles.md) was revised to incorporate these comments. The remaining open item is the scoring appendix and implementation plan.
