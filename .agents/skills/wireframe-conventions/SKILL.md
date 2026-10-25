---
name: "wireframe-conventions"
description: "Use when prototyping or migrating Raidar review-surface layouts through the dedicated wireframe page."
---
# Task

## Rules

- Keep wireframe work in `review-surface/src/pages/WireframeExperimentsPage.tsx` (and any directly related assets used only by that page).
- Treat the wireframe as the dedicated surface for testing interaction and layout hypotheses before any approved migration.
- Keep wireframe changes independent from live review-surface pages and components.
- Never edit or refactor existing components while proving a wireframe change.
- Do not change core-page behavior unless explicitly approved outside a wireframe iteration.

## Migration path

For any migration from existing non wireframe implementation to a new component:

1. Build the equivalent in the wireframe only.
2. Validate behavior, interaction, and content.
3. Apply a scoped refresh based on the existing wireframe design theme, mode of behaviour/interaction, and any user specified actions.

## Iteration checklist

- Identify the exact wireframe target before changing UI behavior.
- Make one atomic UI change batch before returning to review unless a hard dependency requires multiple files.
- After each approved update, save the edit and run from repo root:
  - `make review-surface-build`
  - `make review-surface-serve`
- Verify fresh runtime output at `http://127.0.0.1:5950/wireframe`.
- Use Playwright installed under the review-surface app for the browser check.
- Confirm `/wireframe` renders content, is not blank, and has no console `Error:` output.
- Stop and fix any console exception before reporting completion.

## Presentation

- Start with a compact comparison overview; put exact values, definitions and evidence in overlays.
- Keep labels, metadata and filters concise; defaults name the scope (for example, "all problems").
- Use tables for row comparison, predictable metric widths and text widths suited to content.
  Mark the strongest row only with a deterministic rule based on displayed metrics.
- Use progression circles for numeric metrics, without embedded numbers in the overview.
  Colours: green healthy, amber partial/volatile, red weak/problematic, grey unavailable/zero.
- Use smaller distinct symbols for categorical findings, with enough spacing to avoid overlap.
  Put multiplicity in overlays rather than large count badges.
- Hover opens an ephemeral overlay; clicking pins it. Allow multiple pinned overlays.
  Provide compact icon controls for pin/close and previous/next only for multiple items.
  Show position as "1 of 4"; avoid repeating titles or interaction instructions.
- Header overlays explain the column's meaning, source and use.
- Preserve agreed canonical labels; avoid overlapping names for distinct metrics.

## Output

- Commit each completed wireframe change set atomically, limited to wireframe files unless migration was approved.
- Report changes, validation and whether the rebuilt page loaded correctly.
