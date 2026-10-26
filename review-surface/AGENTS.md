# Review surface

- Preserve the existing user flow and every signal/persona question asserted by Playwright.
  Change assertions only when the flow legitimately changes, retaining coverage of each signal.
- Use problem, arm and trial wording in visible UI.
- Preserve /runs/:runId routes and data-run-id attributes as internal contracts.
- Keep Jev dormant under the policy constraints in `lab/judge/AGENTS.md`.
- After UI changes run `make review-surface-test`.
  Include real jobs with REVIEW_JOBS_ROOT and one REVIEW_STORE for all roots or one per root.
  Tests rewrite projected data; regenerate the intended projection before serving real jobs.
