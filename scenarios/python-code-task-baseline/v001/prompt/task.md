Implement the ledger totals utility in the starter project.

Create `src/ledger_utils/totals.py` and export `summarize_transactions(transactions)`.
The function should accept an iterable of dictionaries with `amount` and `category`
fields and return a dictionary with:

- `income`: sum of positive amounts
- `expenses`: absolute value of summed negative amounts
- `net`: income minus expenses
- `by_category`: category totals preserving expense signs

Raise `ValueError` when a transaction is missing `amount` or `category`, or when
`amount` is not numeric. Add unit tests in `tests/test_totals.py` covering valid
mixed input, empty input, and invalid input.

Run all required verification commands before completion and report only after
they pass.
