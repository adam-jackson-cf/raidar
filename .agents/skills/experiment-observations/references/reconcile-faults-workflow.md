# Reconcile observed faults

1. Send only the candidate evidence from Step 1 to `fault-catalog` (its Analyse
   job and Update Fault Status runbooks) before any fault name appears in the
   report or the concise view.
2. `fault-catalog` alone owns canonical IDs, wording, categories, owners,
   statuses and catalog changes.
3. Require a reconciliation result for every observed fault entry, in the
   `fault-catalog` reconciliation result schema. Keep its wording verbatim.
4. Leave unreconciled evidence out of `Observed fault types`; list it only in the
   report's limitations with its artifact paths.
5. Carry the reconciled entries and changed record IDs into Step 3.
