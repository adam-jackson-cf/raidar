Fix the reported shipping quote defect in the starter project.

Bug report: expedited shipments to zone 3 are undercharged because the surcharge
is not applied. Preserve the public `shipping_quote(weight_kg, zone,
expedited=False)` API and avoid unrelated rewrites.

Add or update regression tests that fail before the fix and pass after it. Keep
the change focused on the shipping quote behavior.

Run all required verification commands before completion and report only after
they pass.
