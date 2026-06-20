---
name: "wireframe-conventions"
description: "Use When you need to do any wireframe work."
---
# Guidance

Use this before and during any wireframe iteration so changes stay isolated from existing experiments pages and remain demonstrably reproducible.

## Scope and location

- Keep wireframe work in `review-surface/src/pages/WireframeExperimentsPage.tsx` (and any directly related assets used only by that page).
- Treat the wireframe as a dedicated exploration surface for rapid UI design changes.
- The purpose is to test interaction and layout hypotheses first, then port validated patterns intentionally when the user gives approval.

## Non-impact rule

- Never edit or refactor existing components on the wireframe while still proving a wireframe change.
- Do not change existing component behavior in core pages unless explicitly approved and requested outside a wireframe iteration.
- Any migration from existing non wireframe implementation to a new component should be done by:
  1. first building the equivalent in the wireframe only,
  2. validating behavior and interaction, and content
  3. then applying a scoped refresh based on the existing wireframe design theme and mode of behaviour/interaction plus any user specified actions.
- Keep wireframe changes independent from the live review-surface pages and components.

## Iteration workflow

- Before changing UI behavior, identify the exact target in the wireframe section and keep edits narrowly scoped.
- When implementing a design change, include only one atomic UI change batch before returning to review unless a hard dependency requires multiple files.
- After each approved wireframe update:
  - Save the edit.
  - Run a full npm rebuild.
  - Verify the browser loads with Playwright which installed under the review-surface app using `http://127.0.0.1:5950/wireframe`.

## Required operational steps

- Always run:
  - `cd /Users/adamjackson/Projects/complete/raidar`
  - `make review-surface-build`
- Restart the service so you know you are seeing fresh runtime output:
  - `make review-surface-serve`
- Confirm the endpoint is available at:
  - `http://127.0.0.1:5950/wireframe`

## Testing/checking for this workflow (mandatory)

- Always run a lightweight runtime load check after each change to catch React lifecycle issues (for example, hook-order regressions like minified React error #310).
- Use a browser session check and verify the page renders without console errors:
  - open or refresh `/wireframe` in headless Playwright and confirm the page content appears, not blank.
  - verify there is no `Error:` output in console.
- If any console exception appears, stop and fix before reporting completion.

## Commit hygiene

- Do one atomic commit after each completed wireframe change set.
- Commit message should be specific and concise, e.g.:
  - `wireframe: tune scenario and revision dropdown interaction`
  - `wireframe: add overlay navigation for issues container`
- Keep commit content limited to wireframe files unless an explicit migration was approved.
- Record what changed, what was validated, and whether rebuild+load check passed.
