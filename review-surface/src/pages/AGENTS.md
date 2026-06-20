# Wireframe UI Rules

These rules guide review-surface wireframes and should be applied to relevant UI components, aligned with the product principles of focused information architecture, zoomed-out comparison first, and detail-on-demand.

## Information Hierarchy

- Start each surface with the smallest useful overview for comparison, then expose detail through overlays or drill-in states.
- Remove text, counts, subtitles, or labels that can be inferred from visible structure or do not materially change the user decision.
- Prefer concise operational labels over descriptive phrases when the context is clear.
- Keep repeated concepts visually compact and consistent across the surface.

## Wireframe Change Workflow

- Rebuild the review-surface app after every wireframe UI change so the in-app browser reflects the latest implementation.
- Use `cd /Users/adamjackson/Projects/complete/raidar/review-surface && npm run build` for wireframe rebuilds.
- Documentation-only changes are rebuild-exempt unless the documentation is rendered inside the browser surface.

## Dropdowns And Filters

- Dropdown defaults should describe the actual scope, such as `all scenarios`, not vague tokens like `all`.
- Do not add adjacent explanatory count labels unless the count changes the user decision or cannot be inferred from visible results.
- Keep filter bars visually quiet: inputs, selectors, and search affordances only.
- Use neutral wording in filters and avoid duplicating the section title in the control area.

## Tables

- Tables should support fast row comparison, not narrative reading.
- Keep row metadata minimal and move repeated or measurable details into dedicated columns.
- Use short column titles and put definitions in hover overlays.
- Metric columns should have fixed or predictable widths and centered indicators.
- Text columns should size around actual content and avoid excessive fixed width when labels are compact.
- If a row needs a "best" or "strongest" indicator, use a familiar icon and a deterministic ranking rule based on the same metrics shown in the table.
- Table rows should avoid redundant counts that are obvious from the visible row set.

## Metrics

- Numeric, scalar, or completion-style metrics should use progression circles as the default overview indicator.
- A progression circle's fill amount represents normalized progress, coverage, quality, or completion for that metric.
- Metric circles should avoid embedded numeric labels in the default view.
- Use green for strong or healthy values, amber for partial, volatile, or borderline values, red for weak or problematic values, and grey for unavailable or zero-state values.
- Metric details belong in hover or pinned overlays, including exact numbers, thresholds, and supporting calculations.
- Add new metric columns only when the signal is meaningfully distinct from existing metrics.

## Categorical Signals

- Categorical indicators should not reuse progression-circle shapes, because their role is different from numeric metrics.
- Use compact symbol or icon badges for categorical signal groups.
- Use shapes such as diamonds for categorical findings so they remain visually distinct from metric circles.
- Keep categorical badges smaller and less prominent than primary metric indicators unless they represent critical blocking state.
- Space categorical badges enough that rotated or shaped indicators do not visually overlap.
- Avoid numeric badges for counts when counts may become large; show multiplicity through the overlay instead.

## Overlays

- Hover should open an ephemeral overlay attached near the trigger or cursor.
- Clicking the trigger should pin the corresponding overlay.
- Multiple overlays may be pinned at the same time.
- Pinned overlays should include compact pin and close controls in the top bar.
- Overlay controls should use icons in the established style, not visible instructional copy.
- Multi-item overlays should show top-bar previous and next controls only when more than one item exists.
- Multi-item overlays should show position as `1 of 4` style text.
- Overlay content should not repeat the same title or type label at both top and bottom.
- Header overlays should explain the column's meaning, source data, and how to use the signal.

## Language

- Prefer short, decision-oriented labels such as `Outcome`, `Stability`, `Trust`, `run`, and `Findings` when they match the user task.
- Rename labels when the current term is vague or overlaps with another metric.
- Do not use visible text to explain interaction mechanics if the icon and overlay affordance can carry it.
- Preserve canonical wording once a label or rule is agreed.
