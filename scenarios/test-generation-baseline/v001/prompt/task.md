Add meaningful tests for the existing pricing utility.

The production implementation in `src/pricing/discounts.py` is already correct.
Do not change production code unless a test exposes a genuine defect. Create
`tests/test_discounts.py` with focused assertions for:

- percentage discounts
- fixed discounts
- invalid negative totals or unsupported discount kinds

Run all required verification commands before completion and report only after
they pass.
