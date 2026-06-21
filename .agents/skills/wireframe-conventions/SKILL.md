---
name: "wireframe-conventions"
description: "Use When you need to do any wireframe work."
---
# Guidance

Use this before and during wireframe iteration to keep exploration isolated, reproducible, and intentionally portable.

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

## Commit hygiene

- Make one atomic commit after each completed wireframe change set.
- Keep commit content limited to wireframe files unless an explicit migration was approved.
- Use a specific, concise message, e.g.:
  - `wireframe: tune scenario and revision dropdown interaction`
  - `wireframe: add overlay navigation for issues container`
- Record what changed, what was validated, and whether rebuild+load check passed.
