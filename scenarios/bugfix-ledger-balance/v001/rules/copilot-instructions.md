# Scenario Rules

- Fix only bug RAID-1042; do not refactor unrelated code or modify unrelated files.
- Keep the exported API of `src/lib/ledger.ts` unchanged.
- Re-enable the skipped reproduction test; never delete or weaken existing tests.
- Place new regression tests in `src/test/ledger.regression.test.ts`.
- Retain defect evidence at `evidence/defect-evidence.json` exactly as the task specifies.
- Do not add TODO placeholders.
- Apply the formatter's exact suggested output if lint fails.
