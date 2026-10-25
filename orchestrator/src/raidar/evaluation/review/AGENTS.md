# Review projection

- Preserve projection schema_version=2 wire fields, including runs, run_json, harness and headline_policy.
- Preserve the user flow and every signal/persona question asserted by the review-surface Playwright suite.
- After projection changes run `make review-surface-test`.
