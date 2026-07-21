Fix the reported shipping quote defect in the starter project.

Bug report: expedited shipments to zone 3 are undercharged because the surcharge
is not applied. Preserve the public `shipping_quote(weight_kg, zone,
expedited=False)` API and avoid unrelated rewrites.

Add or update a regression test that asserts a 2 kg expedited zone 3 shipment
quotes 19.2. Retain `evidence/defect-evidence.json` as a JSON object that names
the changed regression test under `regression_tests`, `test_evidence`, or
`passing_tests`.

Run all required verification commands before completion and report only after
they pass.
