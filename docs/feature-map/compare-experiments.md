# Compare experiments

A benchmark reviewer decides which agent spec delivers best for a problem, and which trial to
read first. This is the Compare stage of the review surface's compare → explain → trace
investigation; [Explain and trace a trial](explain-and-trace-a-trial.md) continues it.

## Sub-features

- **Problem families.** The Experiments page groups specs by problem and revision; the
  "Problem families" anchors jump to a family. End state: at least one family per problem in
  the loaded jobs, each anchor scrolling to its family.
- **Verdict headline.** Each family states the best spec's delivery tier and mean score
  (for example "delivered to spec (0.91)"); with two or more specs it names the runner-up's
  gap ("Best delivery: … trails by …"). When any trial has issues it ends with
  "Start with Trial NN (most issues)" or "(the failing trial)". End state: the link opens that
  trial's run detail.
- **Comparison table.** One row per spec: [Delivery](../review-surface-ux-kb/concepts/delivery-tiers.md),
  [Repeatability](../review-surface-ux-kb/concepts/repeatability.md), Issues,
  [Confidence](../review-surface-ux-kb/concepts/sample-confidence.md), Pace and Tokens.
  Expanding a row shows "Where points were lost" and "What held up", score-area bars,
  evidence-linked findings and [run pills](../review-surface-ux-kb/components/run-pill.md);
  clicking it again collapses it.
- **Cross-checks.** The [trade-off scatter](../review-surface-ux-kb/components/tradeoff-scatter.md)
  opens a trial from a point; [failure patterns](../review-surface-ux-kb/components/failure-patterns.md)
  list recurring gate failures; [revision movement](../review-surface-ux-kb/components/revision-movement.md)
  shows whether a revision helped.
- **Data.** `experiments.json` (aggregates, metric and scorer outcomes, sample adequacy,
  revision diffs) and `runs.json` for the run pills; see
  [projections](../review-surface-ux-kb/data/projections.md).

## How to get to it (user POV)

1. Project jobs into review data and serve the surface: `make review-surface-serve
   REVIEW_JOBS_ROOT=<jobs-root> [REVIEW_STORE=<store>]`.
2. Open `http://localhost:5950/` (the "Experiments" tab of the
   [Experiments page](../review-surface-ux-kb/pages/experiments.md)).
3. Pick a family from the Problems row, read its headline, expand a spec's row, then follow
   "Start with" or a run pill.

Personas: the [benchmark reviewer](../review-surface-ux-kb/personas/benchmark-reviewer.md)
works here and hands off to the eval engineer on run detail.

## Driving it with node scripts/surface/lever.mjs

- Journey: [compare-experiments.journey.mjs](../../review-surface/journeys/compare-experiments.journey.mjs)
  (`preserve`: the outcome exists today).
- Command, from the repo root:
  `node scripts/surface/lever.mjs --data-dir "$(mktemp -d)/data" --evidence-dir <dir>
  --journey review-surface/journeys/compare-experiments.journey.mjs`.
- The lever serves the synthetic review fixture (four problem families). The journey asserts
  at least two families, a "delivered to spec (x.xx)" verdict, the row expansion, and that
  "Start with Trial 04 (most issues)" opens `/runs/synthetic-bugfix-gpt-5.5-low-04` with
  "Why it scored this" visible, with no console or page errors.
- Evidence: `compare-experiments.result.json`, `.junit.xml`, `.log` and `.final.png` in the
  evidence directory, plus the lever's `lever.json` and `doctor.json`.

## Gotchas

- "Best delivery:" and "trails by" appear only in a family with two or more specs. The
  synthetic fixture currently projects one spec per family, so journeys must not wait for them.
- Run ids are fixture-stable (`synthetic-bugfix-gpt-5.5-low-04`); the projected `dir` field
  embeds the data directory, so never record it as an outcome.
- An empty or missing data projection fails the lever's doctor
  (`RAIDAR-SURFACE-DOCTOR-FAILED`), which is an environment block, never a product defect.
