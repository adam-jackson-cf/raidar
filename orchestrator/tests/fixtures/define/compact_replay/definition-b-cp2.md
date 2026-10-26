# Report formats, inclusive date filtering and environment defaults

## Objective

Extend the existing Python report command with JSON output, an inclusive date filter and an environment default for the category limit, retaining part 1's observable behavior when the additions are unused.

## Features

### F1 Report command and option syntax

Outcome: Users can supply the three separate-word options in any order, with last-value validation, while existing report behavior stays intact.
Risk: requires review

Acceptance criteria:
- F1.AC1: The report entry point accepts the expanded usage with options in any order.
  Source: "Extend the `report` command from part 1."
  Source: "The full usage is now:"
  Source: "python tally.py report FILE [--top N] [--format FORMAT] [--since DATE]"
  Source: "Each option is written as the option name followed by a separate value word, and options may appear in any order."
  Aspect: `python tally.py report FILE` receives valid separate values for `--top`, `--format` and `--since` in each permutation -> exit code is `0`
  Aspect: `python tally.py report FILE` receives equivalent option values in different orders -> stdout is identical across permutations
  Aspect: `report FILE` receives an option with its value joined by `=` -> exit code is `2`
  Example: `python tally.py report FILE --top 1 --format text --since 2024-03-01` with a matching food row worth 1 -> stdout is `food: 1.00\n`
  Verification: Add parameterized CLI tests in `tests/test_options.py` covering all option permutations and rejection of joined-value forms.
- F1.AC2: Only the final occurrence of each option supplies the value to validate and use.
  Source: "As with `--top`, if any option is given more than once, only its last value is used (and only that one is checked)."
  Aspect: `report FILE --top invalid --top 1` uses a valid input file -> stdout contains exactly the highest-ranked category
  Aspect: `report FILE --format xml --format json` uses a valid input file -> stdout is the JSON report
  Aspect: `report FILE --since nope --since 2024-03-01` uses rows before and on that date -> stdout contains only the on-date row's category
  Aspect: `report FILE --top 1 --top invalid` uses a valid input file -> stderr is `error: --top must be between 1 and 10\n`
  Aspect: `report FILE --format json --format xml` uses a valid input file -> stderr is `error: --format must be text or json\n`
  Aspect: `report FILE --since 2024-03-01 --since nope` uses a valid input file -> stderr is `error: invalid date 'nope'\n`
  Verification: Add CLI tests in `tests/test_options.py` with earlier invalid and final valid values, and the reverse, including interleaved repeated options.
- F1.AC3: Existing report output, exit codes and diagnostics remain unchanged when the additions are unused.
  Source: "Everything from part 1 keeps working exactly as before: when none of `--format`, `--since` or the `TALLY_TOP` environment variable is used, the output, exit codes and error messages are unchanged."
  Kind: regression
  Verification: Keep the earlier tests in `tests/test_cli.py`, `tests/test_input.py`, `tests/test_amounts.py`, `tests/test_output.py`, `tests/test_ordering.py`, `tests/test_top.py`, `tests/test_empty.py`, `tests/test_errors.py`, `tests/test_example.py` and the behavioral network/clock test in `tests/test_runtime.py` passing with `TALLY_TOP` absent. The structural runtime test retirement is recorded below.

### F2 --top category limit

Outcome: Explicit top limits retain their accepted digit syntax, bounds, sorting and truncation semantics for both formats.

Acceptance criteria:
- F2.AC1: The existing explicit top option continues to work.
  Source: "Everything from part 1 keeps working exactly as before: when none of `--format`, `--since` or the `TALLY_TOP` environment variable is used, the output, exit codes and error messages are unchanged."
  Kind: regression
  Verification: The earlier tests in `tests/test_top.py` continue to prove ASCII digit validation, leading zeros, bounds, default three, sorting before truncation, and last-occurrence validation.

### F3 --format text and json

Outcome: Reports default to the accepted text representation and can instead produce an exact UTF-8 JSON line.

Acceptance criteria:
- F3.AC1: The default format and explicit text format have the same part 1 output.
  Source: "`--format` is `text` or `json`."
  Source: "The default is `text`, which is the part 1 output; `--format text` given explicitly behaves identically."
  Aspect: `report FILE` runs without `--format` on a nonempty valid file -> stdout equals the part 1 text report
  Aspect: `report FILE --format text` runs on the same nonempty valid file -> stdout equals the default report byte for byte
  Aspect: `report FILE` receives `--format` with value `json` on a nonempty valid file -> stdout is a JSON array
  Example: `report FILE --format text` for one food amount of 4.5 -> stdout is `food: 4.50\n`
  Verification: Add CLI comparisons in `tests/test_format.py` for omitted and explicit text format, using the earlier sample and a single-category file.
- F3.AC2: JSON is one newline-terminated array with ordered objects, exact total strings and standard spacing, using the same ranking and top limit as text.
  Source: "With `--format json`, standard output is one line followed by a newline: a JSON array with one object per category, in the same order as the text output and cut by `--top` in the same way."
  Source: "Each object has the keys `category` and `total`, in that order, and `total` is a string with exactly two decimals."
  Source: "Use the separators `, ` between items and `: ` after keys (the default of Python's `json.dumps`), so a line looks like:"
  Source: "[{"category": "rent", "total": "800.00"}, {"category": "food", "total": "19.75"}]"
  Aspect: `report FILE --format json --top 2` aggregates repeated categories and has more than two categories -> stdout has exactly one array item per selected category
  Aspect: `report FILE --format json --top 2` has unequal totals and tied totals -> array category order equals the text report's descending-total and name-tie order
  Aspect: `report FILE --format json` aggregates integer and one-decimal amounts -> each `total` value is a string with exactly two decimals
  Aspect: `report FILE --format json` reports a category -> each object's serialized keys are `category` then `total`
  Aspect: `report FILE --format json` reports multiple categories -> stdout uses separators `, ` and `: ` as in Python's default `json.dumps`
  Aspect: `report FILE --format json` reports multiple categories -> stdout is exactly one line terminated by `\n`
  Example: `report FILE --format json --top 2` with rent 800 and food 19.75 -> stdout is `[{"category": "rent", "total": "800.00"}, {"category": "food", "total": "19.75"}]\n`
  Verification: Add exact-output CLI tests in `tests/test_format.py`; assert raw serialization, parsed string totals, key order, ranking and top truncation, including exact large totals already supported by the repository.
- F3.AC3: JSON escapes required characters while retaining literal non-ASCII UTF-8 characters.
  Source: "Strings are escaped as JSON requires (a `"` in a category becomes `\"`), but non-ASCII characters are written as they are, encoded as UTF-8, never as `\uXXXX` escapes."
  Source: "(Python: `ensure_ascii=False`.)"
  Aspect: `report FILE --format json` reads a category containing `"` -> its serialized category contains `\"`
  Aspect: `report FILE --format json` reads a category containing a backslash -> its parsed JSON category equals the original category
  Aspect: `report FILE --format json` reads category `café` -> stdout contains the literal UTF-8 bytes of `café` using `ensure_ascii=False` semantics
  Aspect: `report FILE --format json` reads category `café` -> stdout contains no `\uXXXX` escape for that category
  Example: `report FILE --format json` with category `café` and amount 1 -> stdout is `[{"category": "café", "total": "1.00"}]\n`
  Example: `report FILE --format json` with category `a"b` and amount 1 -> stdout is `[{"category": "a\"b", "total": "1.00"}]\n`
  Verification: Add byte-level subprocess assertions and JSON round-trip assertions in `tests/test_format.py` for quotes, backslashes and non-ASCII category names.
- F3.AC4: A report with no rows has the specified representation in each format.
  Source: "If there are no rows to report, JSON output is `[]` followed by a newline."
  Source: "(Text output prints `no data` in that case, as in part 1.)"
  Aspect: `report FILE --format json` reads a header-only file -> stdout is `[]\n`
  Aspect: `report FILE --format text` reads a header-only file -> stdout is `no data\n`
  Example: header-only input with `--format json` -> stdout is `[]\n`
  Example: header-only input with `--format text` -> stdout is `no data\n`
  Verification: Add parameterized header-only CLI tests in `tests/test_format.py`, retaining the earlier `tests/test_empty.py` tests.
- F3.AC5: Invalid format values produce only the specified diagnostic and status.
  Source: "Any value other than `text` or `json` prints `error: --format must be text or json` to standard error, nothing to standard output, and exits with code `2`."
  Aspect: `report FILE --format xml` runs with a valid top value -> stderr is `error: --format must be text or json\n`
  Aspect: `report FILE --format xml` runs with a valid top value -> stdout is empty
  Aspect: `report FILE --format xml` runs with a valid top value -> exit code is `2`
  Example: `report FILE --format xml` -> stderr is `error: --format must be text or json\n`, exit code is `2`
  Verification: Add parameterized CLI tests in `tests/test_format.py` for `xml`, uppercase names, whitespace and the empty value.

### F4 --since inclusive date filter

Outcome: A valid calendar threshold filters rows inclusively before aggregation, while every input amount is still validated.

Acceptance criteria:
- F4.AC1: The date filter keeps rows on or after the threshold and excludes earlier rows before calculating totals.
  Source: "`--since DATE` keeps only the rows whose date is on or after DATE."
  Source: "The comparison is inclusive: a row dated exactly DATE is kept."
  Source: "Dates in the file and DATE are written `YYYY-MM-DD`, so they compare as plain strings."
  Aspect: `report FILE` receives `--since DATE` with DATE set to `2024-03-01` and reads earlier and later rows of one category -> the category total includes only rows on or after the threshold
  Aspect: `report FILE --since 2024-03-01` reads a row dated exactly `2024-03-01` -> that row's amount is included in its category total
  Aspect: `report FILE --since 2024-03-01` reads a category found only before the threshold -> that category is absent from stdout
  Example: `report FILE --since 2024-03-01` on `YYYY-MM-DD` rows dated 2024-02-28 and 2024-03-01, both food with amounts 8 and 2 -> stdout is `food: 2.00\n`
  Verification: Add CLI tests in `tests/test_since.py` asserting inclusive selection, removal of entire categories and recalculated ranking in text and JSON.
- F4.AC2: The threshold must have exactly the specified date form and identify a real calendar day; invalid values are reported verbatim.
  Source: "DATE is valid only when it has the form `YYYY-MM-DD` and names a real calendar date (`2024-02-30` and `2024-13-01` are invalid)."
  Source: "Otherwise print `error: invalid date '<value>'` to standard error, with `<value>` exactly as given, print nothing to standard output, and exit with code `2`."
  Aspect: `report FILE --since DATE` receives a value outside the exact ASCII `YYYY-MM-DD` form -> stderr is `error: invalid date '<value>'\n` with `<value>` replaced verbatim
  Aspect: `report FILE --since 2024-02-30` runs with valid earlier options -> stderr is `error: invalid date '2024-02-30'\n`
  Aspect: `report FILE --since 2024-13-01` runs with valid earlier options -> stderr is `error: invalid date '2024-13-01'\n`
  Aspect: `report FILE --since 2024-02-29` reads a row on that leap day -> exit code is `0`
  Aspect: `report FILE --since nope` runs with valid earlier options -> stdout is empty
  Aspect: `report FILE --since nope` runs with valid earlier options -> exit code is `2`
  Example: `report FILE --since 2024-02-30` -> stderr is `error: invalid date '2024-02-30'\n`, exit code is `2`
  Example: `report FILE --since 2024-13-01` -> stderr is `error: invalid date '2024-13-01'\n`, exit code is `2`
  Verification: Add parameterized CLI tests in `tests/test_since.py` for impossible days, leap/non-leap February, invalid months, missing zero padding, whitespace, empty values, non-ASCII digits and trailing data; assert exact diagnostics, streams and status.
- F4.AC3: Invalid amounts remain errors even in rows excluded by the date filter, retaining physical line numbers.
  Source: "Every row's amount is validated (and reported with its file line number, as in part 1) before filtering, so a bad amount in a row that `--since` would drop is still an error, exit code `1`."
  Aspect: `report FILE --since 2024-03-01` reads an invalid amount `abc` on physical line `3` dated before the threshold -> stderr is `error: line 3: invalid amount 'abc'\n`
  Aspect: `report FILE --since 2024-03-01` reads an invalid amount in a row dated before the threshold -> exit code is `1`
  Aspect: `report FILE --since 2024-03-01` reads an invalid amount after a valid retained row -> stdout is empty
  Example: line 3 contains `2024-02-28,food,abc`, filtered by `--since 2024-03-01` -> stderr is `error: line 3: invalid amount 'abc'\n`, exit code is `1`
  Verification: Add CLI tests in `tests/test_since.py` with invalid excluded rows, retained rows and multiple bad rows; assert original physical line numbers and the first invalid amount diagnostic for both formats.
- F4.AC4: Filtering all rows produces a successful empty report in the selected format.
  Source: "If filtering leaves no rows, the empty-input rule applies: text output prints `no data`, JSON output prints `[]`, exit code `0`."
  Aspect: `report FILE --since 2024-03-01 --format text` reads only earlier rows -> stdout is `no data\n`
  Aspect: `report FILE --since 2024-03-01 --format json` reads only earlier rows -> stdout is `[]\n`
  Aspect: `report FILE --since 2024-03-01` reads only earlier valid rows in either format -> exit code is `0`
  Example: all rows precede the threshold with text format -> stdout is `no data\n`, exit code is `0`
  Example: all rows precede the threshold with JSON format -> stdout is `[]\n`, exit code is `0`
  Verification: Add parameterized CLI tests in `tests/test_since.py` asserting full result tuples when every row is excluded.

### F5 TALLY_TOP environment default

Outcome: A nonempty environment setting supplies the category limit only when the command has no explicit top option.

Acceptance criteria:
- F5.AC1: A nonempty environment default obeys the same top syntax and bounds as an explicit option.
  Source: "The environment variable `TALLY_TOP` sets the default for `--top`:"
  Source: "- If `--top` is not given and `TALLY_TOP` is set to a non-empty value, that value is used as N, and it must be valid by the same rule as `--top` (only digits, value 1 to 10)."
  Aspect: `report FILE` has no `--top` and inherits `TALLY_TOP=1` on a file with several categories -> stdout contains exactly the highest-ranked category
  Aspect: `report FILE` has no `--top` and inherits `TALLY_TOP=01` -> stdout equals the report with explicit `--top 1`
  Aspect: `report FILE` has no `--top` and inherits `TALLY_TOP=10` on a file with more than ten categories -> stdout contains exactly the highest-ranked ten categories
  Example: `TALLY_TOP=1` with no `--top` and the earlier sample -> stdout is `rent: 800.00\n`
  Example: `TALLY_TOP=10` with eleven categories -> reported category count is `10`
  Verification: Add CLI tests in `tests/test_environment.py` setting the environment through pytest monkeypatch, comparing text and JSON category selection against explicit top limits.
- F5.AC2: Explicit top ignores the environment entirely, including invalid settings.
  Source: "- If `--top` is given, it takes precedence and `TALLY_TOP` is ignored entirely, even when `TALLY_TOP` is invalid."
  Aspect: `report FILE --top 2` inherits `TALLY_TOP=invalid` -> stdout equals the explicit top-two report with the variable absent
  Aspect: `report FILE --top 1` inherits `TALLY_TOP=10` -> stdout contains exactly the highest-ranked category
  Aspect: `report FILE --top invalid` inherits `TALLY_TOP=1` -> stderr is `error: --top must be between 1 and 10\n`
  Verification: Add override tests in `tests/test_environment.py` with valid and invalid environment settings, valid and invalid explicit top values, and repeated top values.
- F5.AC3: Invalid environment defaults have their own diagnostic and no output.
  Source: "- If `--top` is not given and `TALLY_TOP` is invalid, print `error: TALLY_TOP must be between 1 and 10` to standard error, nothing to standard output, and exit with code `2`."
  Aspect: `report FILE` has no `--top` and inherits an invalid nonempty `TALLY_TOP` -> stderr is `error: TALLY_TOP must be between 1 and 10\n`
  Aspect: `report FILE` has no `--top` and inherits an invalid nonempty `TALLY_TOP` -> stdout is empty
  Aspect: `report FILE` has no `--top` and inherits an invalid nonempty `TALLY_TOP` -> exit code is `2`
  Example: `TALLY_TOP=11` without `--top` -> stderr is `error: TALLY_TOP must be between 1 and 10\n`, exit code is `2`
  Verification: Add parameterized tests in `tests/test_environment.py` using the existing invalid top forms, including signs, whitespace, Unicode digits, zero, eleven and a very long digit string.
- F5.AC4: Empty and absent environment values fall back to three categories.
  Source: "- An empty `TALLY_TOP` counts as not set."
  Source: "- If neither is given, N is `3`."
  Aspect: `report FILE` has no `--top` and inherits `TALLY_TOP` as the empty string on a file with more than three categories -> stdout equals the default top-three report
  Aspect: `report FILE` has no `--top` and no `TALLY_TOP` on a file with more than three categories -> stdout contains exactly the highest-ranked `3` categories
  Example: empty `TALLY_TOP` without `--top` on a file with four categories -> reported category count is `3`
  Example: absent `TALLY_TOP` without `--top` on a file with four categories -> reported category count is `3`
  Verification: Add absent/empty environment CLI tests in `tests/test_environment.py` for text and JSON and compare against explicit `--top 3`.

### F6 Order of checks

Outcome: The command reports one error using the prescribed option and input priority before producing any report.

Acceptance criteria:
- F6.AC1: Top, format and since validation precede file reads, in that order; file-read and amount diagnostics retain their order after valid options.
  Source: "Option errors are reported before FILE is read, and only the first error is reported, in this order: the top value (`--top`, or `TALLY_TOP` when `--top` is absent), then `--format`, then `--since`, then FILE cannot be read, then invalid amounts."
  Source: "For example `--format xml --since nope` reports the `--format` error."
  Aspect: `report FILE --top 0 --format xml --since nope` targets an unreadable file -> stderr is only `error: --top must be between 1 and 10\n`
  Aspect: `report FILE --format xml --since nope` has no `--top`, inherits invalid `TALLY_TOP` and targets an unreadable file -> stderr is only `error: TALLY_TOP must be between 1 and 10\n`
  Aspect: `report FILE --format xml --since nope` has valid top resolution and targets an unreadable file -> stderr is only `error: --format must be text or json\n`
  Aspect: `report FILE --since nope` has valid top and format and targets an unreadable file -> stderr is only `error: invalid date 'nope'\n`
  Aspect: `report FILE` has invalid options and a guarded file-open entry point -> the file-open call count is `0`
  Aspect: `report FILE --format json --since 2024-03-01` targets an unreadable file with valid top resolution -> stderr is only `error: cannot read FILE\n` with the supplied filename substituted
  Aspect: `report FILE --format json --since 2024-03-01` reads a file with multiple invalid amounts after valid options -> stderr contains only the first invalid amount's physical-line diagnostic
  Example: `report FILE --format xml --since nope` with valid top resolution -> stderr is `error: --format must be text or json\n`
  Verification: Add CLI combinations and an in-process forbidden-open guard in `tests/test_validation_order.py`, following `tests/test_top.py` and `tests/test_errors.py`; assert full status/stdout/stderr tuples and no file access for option failures, including reordered options.

### F7 Worked example

Outcome: Combining filtering, JSON and truncation reproduces the issue's complete sample exactly.

Acceptance criteria:
- F7.AC1: The supplied sample produces food then travel after inclusive filtering and top-two truncation.
  Source: "Given `sample.csv`:"
  Source: "date,category,amount"
  Source: "2024-02-28,rent,800"
  Source: "2024-03-01,food,12.50"
  Source: "2024-03-02,food,7.25"
  Source: "2024-03-02,books,4.5"
  Source: "2024-03-05,travel,19.75"
  Source: "Running `report sample.csv --since 2024-03-01 --format json --top 2` prints (exit code `0`):"
  Source: "[{"category": "food", "total": "19.75"}, {"category": "travel", "total": "19.75"}]"
  Source: "The `rent` row is dated before `2024-03-01`, so it is dropped, while the `food` row dated exactly `2024-03-01` is kept."
  Source: "`food` and `travel` both total `19.75`, so they are ordered by name."
  Source: "`books` (`4.50`) is cut by `--top 2`."
  Aspect: `report sample.csv --since 2024-03-01 --format json --top 2` reads the exact five-row sample quoted above -> stdout is `[{"category": "food", "total": "19.75"}, {"category": "travel", "total": "19.75"}]\n`
  Aspect: `report sample.csv --since 2024-03-01 --format json --top 2` reads that sample -> exit code is `0`
  Aspect: `report sample.csv --since 2024-03-01 --format json --top 2` reads that sample -> the array omits `rent`
  Aspect: `report sample.csv --since 2024-03-01 --format json --top 2` reads that sample -> `food` has total `19.75`, including its boundary-date row
  Aspect: `report sample.csv --since 2024-03-01 --format json --top 2` reads that sample -> the category order is `food`, `travel`
  Aspect: `report sample.csv --since 2024-03-01 --format json --top 2` reads that sample with `books` totaling `4.50` -> the array omits `books`
  Example: exact five-row `sample.csv` with `--since 2024-03-01 --format json --top 2` -> stdout is `[{"category": "food", "total": "19.75"}, {"category": "travel", "total": "19.75"}]\n`, exit code is `0`
  Verification: Add a separate part 2 sample test in `tests/test_example.py` asserting the full result tuple without changing the earlier part 1 fixture or its expected report.

## Decisions and assumptions

- Preserve exact legacy usage-error text, including `usage: tally.py report FILE [--top N]`, even though valid command syntax expands. The issue's full usage specifies accepted invocation syntax; it does not explicitly replace usage-error messages. Existing tests assert this diagnostic. (source: rung 1: `tests/test_errors.py:45`, `tests/test_top.py:19`; rung 2: "Everything from part 1 keeps working exactly as before: when none of `--format`, `--since` or the `TALLY_TOP` environment variable is used, the output, exit codes and error messages are unchanged.")
- Unknown options, missing option values, malformed option pairs, missing FILE and unknown commands retain usage-error handling, exit code 2 and empty stdout. Apply structural syntax checks before semantic option-value checks. (source: rung 1: `tally.py:10`, `tests/test_errors.py:36`, `tests/test_top.py:19`.)
- Retain exact decimal arithmetic, arbitrary accepted amount widths, category identity, descending numeric totals and Unicode code-point name tie-breaking; apply the existing aggregation/ranking rules to retained rows for both formats. (source: rung 1: `tally.py:50`, `tests/test_amounts.py:73`, `tests/test_amounts.py:79`, `tests/test_amounts.py:92`, `tests/test_output.py:25`, `tests/test_ordering.py:27`.)
- Keep file dates unchecked, including arbitrary date strings accepted in part 1; validate only the command's DATE argument and compare row date strings directly when filtering. Do not introduce file-date rejection. (source: rung 1: `tests/test_input.py:7`, `tests/test_input.py:12`; rung 2: "Dates in the file and DATE are written `YYYY-MM-DD`, so they compare as plain strings.")
- Environment limits use the existing ASCII-digit rule, allow leading zeros and avoid unbounded conversion before range validation. An explicit top option suppresses all environment validation, even when the explicit value itself is invalid. (source: rung 1: `tally.py:20`, `tests/test_top.py:53`, `tests/test_top.py:68`; rung 2: "- If `--top` is given, it takes precedence and `TALLY_TOP` is ignored entirely, even when `TALLY_TOP` is invalid.")
- Assumption: command dates use ASCII digits, exactly four year digits, two month digits and two day digits, with Gregorian years 0001 through 9999 as supported by Python's standard-library date type. This supplies calendar validation without a clock read. Reverse by changing the date validator and its boundary tests if a different calendar/year range is required. (source: rung 4: default, Python standard-library calendar-date best practice; rung 2: "DATE is valid only when it has the form `YYYY-MM-DD` and names a real calendar date (`2024-02-30` and `2024-13-01` are invalid).")
- Reuse the report's existing parsing, aggregation and rendering flow rather than introducing another executable or a third-party dependency; keep Python 3.12, standard-library runtime and no network or clock access. Internal signatures may change to carry dates and selected options. (source: rung 1: `pyproject.toml:4`, `tests/test_cli.py:7`, `tests/test_runtime.py:15`, `tests/test_runtime.py:64`, `tally.py:67`.)
- retires `tests/test_runtime.py::test_f1_ac1_3_standard_library_runtime`: the issue explicitly extends the command, requiring new option/environment/serialization operations beyond the test's frozen import and call sets (source: rung 2: "Extend the `report` command from part 1."; "The environment variable `TALLY_TOP` sets the default for `--top`:"; "With `--format json`, standard output is one line followed by a newline: a JSON array with one object per category, in the same order as the text output and cut by `--top` in the same way.") requires review. Evidence: `tests/test_runtime.py:26` and `tests/test_runtime.py:31` assert exact implementation sets. Replace that structural test with meaningful standard-library-only validation that permits the requested additions, and keep `tests/test_runtime.py::test_f1_ac2_1_no_network_or_clock` passing. This retires implementation constraints, not observable part 1 behavior.
- Retain earliest-invalid-amount diagnostics, no partial output, physical CSV line numbering, and existing UTF-8 file/read-failure behavior. Amount syntax is checked for every row regardless of filtering or top truncation. Do not expand the CSV format or introduce unrelated malformed-file semantics. (source: rung 1: `tally.py:30`, `tests/test_errors.py:26`, `tests/test_errors.py:57`, `tests/test_errors.py:64`, `tests/test_errors.py:94`, `tests/test_errors.py:100`; rung 2: "Every row's amount is validated (and reported with its file line number, as in part 1) before filtering, so a bad amount in a row that `--since` would drop is still an error, exit code `1`.")
- Add tests only in the existing root `tests/` layout; use subprocess timeouts and inherit the process environment when an explicit environment dictionary is needed. Control only `TALLY_TOP` in environment tests, never display unrelated environment values. Cover every changed production line and satisfy ruff, strict mypy and the verify gate. (source: rung 1: `AGENTS.md:12`, `AGENTS.md:20`, `AGENTS.md:24`, `tests/conftest.py:13`.)
- This intake writes only this definition and performs no implementation, branch, commit or push action. Implementation verification remains the builder's work; source/test inspection is the evidence for this definition. (source: rung 2: "This is a fixture workspace for a benchmark task: there is no branch, no remote and no pull request, so do not branch or push anything.")
