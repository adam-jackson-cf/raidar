# Explain and trace a trial

An eval engineer learns why one trial scored as it did, and a harness debugger follows the
evidence down to the span where it broke and annotates it. These are the Explain and Trace
stages after [Compare experiments](compare-experiments.md).

## Sub-features

- **Verdict banner and gates.** [Run detail](../review-surface-ux-kb/pages/run-detail.md)
  opens with a one-sentence outcome, a tier ("Strong", "Shaky", "Unscored") and
  [gate chips](../review-surface-ux-kb/components/gate-chips.md); "Compare agent specs" returns
  to the trial's family. An unscored trial shows "This trial was not scored" and why.
- **Scorecard and findings.** The [scorecard](../review-surface-ux-kb/components/scorecard-panel.md)
  ("Why it scored this") gives each score area's share; each check and each finding's "jump:"
  button selects its evidence span (the URL gains `span=`).
- **Span tree.** The [span tree](../review-surface-ux-kb/components/span-tree.md) walks agent
  trace → gates → scoring; "Cycle through error spans" selects the next error span; collapse,
  expand and keyboard navigation work; [span detail](../review-surface-ux-kb/components/span-detail.md)
  shows and copies the full payload.
- **Search.** The [search panel](../review-surface-ux-kb/components/search-panel.md) finds plain
  or regex matches across the trial's payloads ("N matches"); a result selects its span.
- **Annotations.** The [create form](../review-surface-ux-kb/components/annotation-create-form.md)
  records an issue, good or note on the selected span ("Annotate span") or the trial
  ("Annotate trial"). End state: the annotation is stored in the instance's
  `user-annotations.json`, survives a reload, and is gone from the page and the file after
  "Delete annotation". Generated findings cannot be deleted.
- **Data.** `runs/<id>.json` (run record, projected spans, findings as annotations) plus
  `user-annotations.json`; see [owned data](../review-surface-ux-kb/data/owned-data.md).

## How to get to it (user POV)

1. Serve the surface (`make review-surface-serve REVIEW_JOBS_ROOT=<jobs-root>`).
2. From the Experiments page follow "Start with" or a run pill, or open the Trials tab
   ([runs index](../review-surface-ux-kb/pages/runs-index.md)) and filter the sidebar.
3. Read the banner and scorecard, jump to evidence, then walk, cycle or search the span tree
   and annotate the span that explains the failure.

Personas: the [eval engineer](../review-surface-ux-kb/personas/eval-engineer.md) explains;
the [harness debugger](../review-surface-ux-kb/personas/harness-debugger.md) traces. Their
annotations feed back to the reviewer's spec decision.

## Driving it with node scripts/surface/lever.mjs

- Journey: [trace-and-annotate.journey.mjs](../../review-surface/journeys/trace-and-annotate.journey.mjs)
  (`preserve`).
- Command: `node scripts/surface/lever.mjs --data-dir "$(mktemp -d)/data" --evidence-dir <dir>
  --journey review-surface/journeys/trace-and-annotate.journey.mjs`.
- On `synthetic-bugfix-gpt-5.5-low-04` the journey cycles to the first error span
  (`…:s21`), searches "ledger" (33 matches), selects the last result (`…:s40`), annotates that
  span as an issue, checks the stored record, reloads, deletes it and checks the file is empty.
- Evidence: `trace-and-annotate.result.json`, `.junit.xml`, `.log`, `.final.png`.

## Gotchas

- With a span selected the submit button reads "Annotate span", otherwise "Annotate trial".
- A new annotation is shown twice (trial list and span detail); locate it with `.first()`.
- Annotations write to the served data directory. Drive only a lever instance, never a
  `make review-surface-serve` instance holding a reviewer's annotations.
