Refactor the report builder in the starter project without changing behavior.

The current `build_report(rows)` implementation works but mixes validation,
grouping, totals, and formatting in one function. Preserve the public API and
the existing output format. Extract focused helpers so the grouping or formatting
logic is easier to review.

Do not change the expected test outputs. Add narrowly scoped tests only if they
clarify preserved behavior.

Run all required verification commands before completion and report only after
they pass.
