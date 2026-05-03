# Benchmark Review Playwright Verification Rubric

Use this rubric in a fresh subcontext to verify the live Raidar benchmark review page. Run against the current app served by `make run-web` from the repo root. Do not inspect `docs/`.

## Objective

Judge whether the web app satisfies the benchmark review UX requirements for:

- How AgentSpecs scored across a benchmark.
- How AgentSpecs performed across revisions from first to last.
- Scenario revision diff comparison showing what changed.
- At-a-glance signals aligned with Raidar's objective: choosing harness + model pairs by delivery quality, reliability, and efficiency against scenario contracts.
- Deeper drilldowns for evidence, metric outcomes, failures, and artifacts.
- Additional UX improvements from the prior review, while preserving the dark Axiom-style theme.

## Setup

1. From repo root, run `make run-web` in a persistent terminal.
2. Load the served URL in Playwright using desktop viewport `1600x900`.
3. Also test mobile viewport `390x844`.
4. Use live `benchmark-view/src/data.json`; do not mock data.
5. Exercise controls through user input/clicks, not by mutating app state directly.

## Pass Criteria

All critical checks must pass. Advisory checks may produce findings but should not block unless they hide or break a core workflow.

## Critical Checks

### C1 Decision Summary

- The first meaningful panel after filters is `Decision summary`.
- It shows the current selection count.
- It includes scenario metadata: scenario, revision, description, category, difficulty, timeout, metric count, gate count, deterministic check count, and visual baseline badge when present.
- It shows these four at-a-glance decisions: `Best AgentSpec`, `Most reliable`, `Fastest acceptable`, `Best value`.
- Each decision card names an AgentSpec and includes supporting numeric evidence.

Evidence to capture: desktop screenshot above the fold and text extraction from the decision section.

### C2 AgentSpec Leaderboard

- `AgentSpec leaderboard` is present above charts.
- Rows are ranked and include AgentSpec, decision score, composite score, valid rate, gate pass rate, duration, tokens, and sample confidence.
- Sorting works for at least `Decision`, `Score`, `Gates`, `Duration`, and `Tokens`.
- Compare checkboxes can select 2-4 rows and open `Compare selected AgentSpecs`.
- Compare drawer includes side-by-side score, validity, gates, duration, tokens, and evidence path.

Evidence to capture: click sorting buttons, select rows, verify drawer text.

### C3 Scenario and Revision Navigation

- Scenario filter supports typing and option selection.
- Revision filter supports typing and option selection.
- AgentSpec filter supports typing and option selection.
- Scenario tabs change the active scenario and update visible data.
- Revision tabs filter visible rows and update leaderboard/chart/table content.
- Clear buttons reset filters.
- Escape closes open option menus.
- Tab or Enter accepts inline completion.

Evidence to capture: test `skill-benchmark-coding-test`, `v003`, and `gpt-5.4-mini` live examples.

### C4 Revision Performance

- `Revision trajectory` shows multi-revision AgentSpec chains when available.
- It includes first-to-last movement, not only one adjacent pair.
- It shows per-revision score values for `skill-benchmark-coding-test` across `v001`, `v002`, and `v003`.
- `Revision deltas` includes both adjacent and first-to-last entries where data supports them.
- Delta rows include score delta, duration delta, token delta, and whether the comparison is adjacent or first-to-last.

Evidence to capture: filter to `skill-benchmark-coding-test` and verify v001-to-v003 movement.

### C5 Scenario Revision Diff

- `Scenario revision diff` exists.
- Diff pair tabs are present for scenarios with adjacent revisions.
- Diff file tabs include `Prompt` and `Scenario YAML`.
- The prompt diff for `skill-benchmark-coding-test v001→v002` shows added verification guidance and implementation details.
- The scenario YAML diff shows scenario contract changes.
- Summary badges disclose prompt/contract changes and comparison warnings where applicable.

Evidence to capture: text extraction from diff panel and screenshot of both Prompt and Scenario YAML diff states.

### C6 Failure and Data Health

- `Failure clusters` groups at least invalid, gate unstable, unscored, sparse sample, slow, and token-heavy conditions.
- Counts reflect current filtered data.
- Unscored artifacts are not silently lost from health reporting.
- `Attention` metric appears in the decision summary.

Evidence to capture: initial unfiltered state and a scenario-specific filtered state.

### C7 Metric Breakdown and Evidence

- `Metric breakdown` exists.
- It shows metric outcome rows when available.
- If detailed metric outcomes are missing, it clearly states that and lists profile metrics.
- `Evidence paths` lists artifact summary, experiment, report, or artifact root paths for top rows.
- Evidence paths correspond to real rows in live data.

Evidence to capture: initial state and selected/filtered state.

### C8 Sortable Run Table

- `Benchmark runs` table remains present.
- It is sortable by scenario, revision, AgentSpec, score, quality, gates, duration, tokens, and runs.
- It displays live rows consistent with filters.
- It is no longer only sorted by latest artifact time.

Evidence to capture: sort by score and duration and verify row order changes.

### C9 Visual Theme Compatibility

- The interface preserves the dark Axiom-style theme: black/graphite surfaces, mono typography, orange attention/accent, sharp low-radius panels.
- It avoids generic AI dashboard patterns: decorative hero copy, soft gradients, oversized radii, pill overload, glassmorphism, fake decorative charts.
- Status colors remain restrained and readable.
- Chart labels and threshold lines are legible.

Evidence to capture: desktop screenshot and mobile screenshot.

### C10 Mobile Usability

- At `390x844`, filters, decision summary, leaderboard, revision trajectory, diff, and table are usable without horizontal page scroll.
- The table/card layout includes data labels.
- The page may scroll vertically, but core decisions should appear before deep evidence and raw run table.
- Autocomplete menus fit within the viewport width.

Evidence to capture: mobile screenshot above fold and after scrolling to leaderboard/table.

## Advisory Checks

### A1 Accessibility

- Autocomplete fields expose combobox/listbox roles.
- Options are reachable by mouse and keyboard.
- Buttons have usable accessible labels.
- Focus states are visible.

### A2 Data Comparability

- Scenario diffs disclose when evaluation profile, gates, deterministic checks, or visual baseline changed.
- Revision comparison warns users not to over-trust changed contracts.

### A3 Performance and Robustness

- The page loads from static files without console errors.
- Filtering and sorting remain responsive with current live data.
- Empty states are meaningful for no-match filters.

## Required Live Examples

Verify these examples explicitly:

- Initial unfiltered dashboard.
- `skill-benchmark-coding-test` + `gpt-5.4-mini`, showing `v001`, `v002`, `v003` trajectory and first-to-last delta.
- `homepage-implementation`, showing diff availability and risk/failure clusters.
- A no-result filter state, such as scenario `skill` plus AgentSpec `claude`, showing useful empty states.

## Report Format

Return:

1. `PASS` or `FAIL` overall.
2. Critical checklist with `pass/fail` per item `C1` through `C10`.
3. Advisory findings `A1` through `A3`.
4. Exact live examples tested.
5. Screenshots or extracted text evidence summary.
6. Ordered remediation list for any failures.

Do not mark overall `PASS` if any critical check fails.
