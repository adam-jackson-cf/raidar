Fix bug RAID-1042 in the ledger utility with minimal, contained changes.

Bug report:

- `ledgerBalanceCents` in `src/lib/ledger.ts` adds debit entries to the balance instead of subtracting them.
- A ledger holding a 5000-cent credit and a 1500-cent debit must produce `3500`, not `6500`.
- A reproduction test already exists in `src/test/ledger.test.ts` but is currently skipped.

Deliverables:

1. Re-enable the skipped reproduction test (remove `.skip`) and confirm it fails before your fix.
2. Fix the defect in `src/lib/ledger.ts`. Keep the exported API unchanged.
3. Add a dedicated regression suite at `src/test/ledger.regression.test.ts` covering debit handling: mixed credits and debits, a debit-only ledger, and a ledger that goes negative.
4. Retain defect evidence at `evidence/defect-evidence.json` as a JSON object with exactly these keys:
   - `reproduction_note`: string describing how you reproduced the defect and the wrong result you observed.
   - `regression_tests`: array of workspace-relative test file paths that protect the fix.
   - `verification_evidence`: string summarising the verification commands you ran and their final results.
5. Do not modify unrelated files. Do not add TODO markers.

Run after the fix: `bun run typecheck`, `bun run lint`, `bun run test`, and `bun run test:coverage`. All must pass. If lint fails, apply its exact formatting/import-order suggestion and rerun only the failed command.
