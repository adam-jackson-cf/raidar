# Report formats, inclusive date filtering and environment defaults

## Objective

Extend the existing Python `tally.py report` command with text/JSON selection, an inclusive date cutoff, and an environment default for the category limit. Preserve part 1 behavior when the new controls are unused, including exact decimal aggregation, ordering, UTF-8 output and error contracts.

## Features

### F1 Report command and FILE input

Outcome: The existing report entry point accepts the extended separate-word options in any order while retaining accepted part 1 behavior.

Acceptance criteria:

- F1.AC1: The report command accepts FILE followed by any ordering of the optional top, format and date arguments, each with a separate value word.
  Source: "Extend the `report` command from part 1."
  Source: "The full usage is now:"
  Source: "python tally.py report FILE [--top N] [--format FORMAT] [--since DATE]"
  Source: "Each option is written as the option name followed by a separate value word, and options may appear in any order."
  Aspect: `python tally.py report FILE` reads the worked-example CSV with each permutation of `--top 2`, `--format json` and `--since 2024-03-01` as separate words -> stdout is `[{"category": "food", "total": "19.75"}, {"category": "travel", "total": "19.75"}]\n`
  Aspect: `python tally.py report FILE` reads the worked-example CSV with each permutation of the three separate-word options -> the command exits with code `0`
  Verification: `tests/test_tally_part2.py::test_option_order` parametrizes all six permutations and asserts exact stdout, exit code and empty stderr.
- F1.AC2: When the new controls are unused, all accepted part 1 output, exit codes and error messages remain unchanged.
  Source: "Everything from part 1 keeps working exactly as before: when none of `--format`, `--since` or the `TALLY_TOP` environment variable is used, the output, exit codes and error messages are unchanged."
  Kind: regression
  Verification: All earlier tests in `tests/test_tally.py` keep passing with TALLY_TOP absent, including invocation, exact decimal arithmetic, amount grammar, category identity, UTF-8 bytes, ordering, top validation, file failures, header-only output and unsupported invocations.

### F2 --top and repeated option resolution

Outcome: The accepted category limit remains available, and every supported option uses and validates only its final occurrence.

Acceptance criteria:

- F2.AC1: For top, format and since independently, a repeated option uses only its last value; an earlier invalid value has no effect.
  Source: "As with `--top`, if any option is given more than once, only its last value is used (and only that one is checked)."
  Aspect: `report FILE --top abc --top 2` reads three distinct categories -> stdout contains exactly `2` category lines
  Aspect: `report FILE --format xml --format json` reads one category -> stdout is the JSON representation of that category
  Aspect: `report FILE --since nope --since 2024-03-01` reads one earlier row and one cutoff-date row -> stdout contains only the cutoff-date category
  Aspect: `report FILE --top 2 --top abc` reads valid CSV -> stderr is `error: --top must be between 1 and 10\n`
  Aspect: `report FILE --format json --format xml` reads valid CSV -> stderr is `error: --format must be text or json\n`
  Aspect: `report FILE --since 2024-03-01 --since nope` reads valid CSV -> stderr is `error: invalid date 'nope'\n`
  Example: `report FILE --top abc --top 2` with three categories -> `2` category lines
  Verification: `tests/test_tally_part2.py::test_repeated_options` parametrizes valid-last and invalid-last occurrences for each option, including occurrences interleaved with other options; asserts exact output, stderr and exit code. Earlier repeated-top tests in `tests/test_tally.py` also pass.

### F3 --format, text output and JSON output

Outcome: Reports default to the accepted text output, or render deterministic UTF-8 JSON with exact two-decimal string totals.

Acceptance criteria:

- F3.AC1: Omitted format and explicit text produce identical part 1 text output.
  Source: "The default is `text`, which is the part 1 output; `--format text` given explicitly behaves identically."
  Aspect: `report FILE` reads populated CSV with TALLY_TOP absent -> stdout equals the accepted `text` report bytes
  Aspect: `report FILE --format text` reads the same populated CSV -> stdout equals the report bytes without `--format`
  Example: `report FILE --format text` with `2024-03-01,food,4.5` -> `food: 4.50\n`
  Verification: `tests/test_tally_part2.py::test_text_format_equivalence` compares explicit text to default text for populated input, Unicode categories, tied totals and top limits; asserts bytes and status against the accepted outputs.
- F3.AC2: JSON is a single newline-terminated array, with one category object per selected category, the accepted ordering and top limit, category before total, two-decimal string totals, and Python default JSON separators.
  Source: "`--format` is `text` or `json`."
  Source: "With `--format json`, standard output is one line followed by a newline: a JSON array with one object per category, in the same order as the text output and cut by `--top` in the same way."
  Source: "Each object has the keys `category` and `total`, in that order, and `total` is a string with exactly two decimals."
  Source: "Use the separators `, ` between items and `: ` after keys (the default of Python's `json.dumps`), so a line looks like:"
  Source: "[{"category": "rent", "total": "800.00"}, {"category": "food", "total": "19.75"}]"
  Aspect: `report FILE` uses `--format` value `json` and `--top` value `2` for rent totaling 800 and food totaling 19.75 -> stdout is `[{"category": "rent", "total": "800.00"}, {"category": "food", "total": "19.75"}]\n`
  Aspect: `report FILE --format json` reads one category -> its object keys are `category`, then `total`
  Aspect: `report FILE --format json` reads multiple categories -> stdout uses `json.dumps` default separators `, ` between items and `: ` after keys
  Aspect: `report FILE --format json` reads categories also reported using `--format text` -> its category order equals the `text` report order
  Aspect: `report FILE --format json` reads tied totals and more categories than the selected limit -> the array category sequence equals the text report category sequence with the same `--top`
  Aspect: `report FILE --format json` reads fractional and large exact amounts -> each `total` field is a string representing its exact sum with two decimals
  Example: `report FILE --format json --top 2` for rent 800 and food 19.75 -> `[{"category": "rent", "total": "800.00"}, {"category": "food", "total": "19.75"}]\n`
  Verification: `tests/test_tally_part2.py::test_json_shape_order_and_limit` asserts exact bytes, ordered object keys and string totals; parametrizes top limits, ties and exact large decimal sums.
- F3.AC3: JSON escapes category strings while emitting non-ASCII characters directly as UTF-8.
  Source: "Strings are escaped as JSON requires (a `"` in a category becomes `\"`), but non-ASCII characters are written as they are, encoded as UTF-8, never as `\uXXXX` escapes."
  Source: "(Python: `ensure_ascii=False`.)"
  Aspect: `report FILE --format json` reads a category containing `"` -> its JSON category contains `\"`
  Aspect: `report FILE --format json` reads a category containing a backslash -> its decoded JSON category equals the original category
  Aspect: `report FILE --format json` reads category `café` under an ASCII stdout configuration -> stdout contains the UTF-8 bytes of `café`
  Aspect: `report FILE --format json` reads category `café` -> stdout contains no `\uXXXX` character escapes, matching `ensure_ascii=False`
  Example: `report FILE --format json` for category `café` totaling 1 -> `[{"category": "café", "total": "1.00"}]\n`
  Verification: `tests/test_tally_part2.py::test_json_escaping_and_utf8` checks exact bytes and decoded category round trips for quotes, backslashes and Unicode, including the ASCII-output environment used by earlier output-contract tests.
- F3.AC4: An empty report emits the format's exact empty representation and newline.
  Source: "If there are no rows to report, JSON output is `[]` followed by a newline."
  Source: "(Text output prints `no data` in that case, as in part 1.)"
  Aspect: `report FILE --format json` reads a header-only CSV -> stdout is `[]\n`
  Aspect: `report FILE --format text` reads a header-only CSV -> stdout is `no data\n`
  Example: `report FILE --format json` with a header-only CSV -> `[]\n`
  Verification: `tests/test_tally_part2.py::test_empty_format_output` asserts exact empty stdout, success status and empty stderr for default text, explicit text and JSON.
- F3.AC5: An invalid final format returns the exact option error with no stdout and exit code 2.
  Source: "Any value other than `text` or `json` prints `error: --format must be text or json` to standard error, nothing to standard output, and exits with code `2`."
  Aspect: `report FILE --format xml` is invoked -> stderr is `error: --format must be text or json\n`
  Aspect: `report FILE --format xml` is invoked -> stdout is empty
  Aspect: `report FILE --format xml` is invoked -> the command exits with code `2`
  Example: `report FILE --format xml` -> stderr `error: --format must be text or json\n`, exit code `2`
  Verification: `tests/test_tally_part2.py::test_invalid_format` parametrizes unsupported, empty, case-varied and whitespace-padded values and asserts exact stderr, empty stdout and status.

### F4 --since date filter

Outcome: An inclusive validated calendar-date cutoff filters rows before aggregation, while all row amounts remain subject to the accepted validation.

Acceptance criteria:

- F4.AC1: Since includes dates equal to or after the cutoff and excludes earlier dates using YYYY-MM-DD string comparison.
  Source: "`--since DATE` keeps only the rows whose date is on or after DATE."
  Source: "The comparison is inclusive: a row dated exactly DATE is kept."
  Source: "Dates in the file and DATE are written `YYYY-MM-DD`, so they compare as plain strings."
  Aspect: `report FILE` uses `--since DATE` with DATE `2024-03-01` and reads valid `YYYY-MM-DD` rows before, on and after the cutoff -> the report category totals equal sums of rows on or after the cutoff
  Aspect: `report FILE --since 2024-03-01` reads a category occurring only before the cutoff -> that category is absent from stdout
  Aspect: `report FILE --since 2024-03-01` reads a row dated exactly `2024-03-01` -> the row's amount is included in its reported category total
  Example: `report FILE --since 2024-03-01` with `2024-03-01,food,12.50` -> `food: 12.50\n`
  Verification: `tests/test_tally_part2.py::test_inclusive_since` asserts exact report totals and category membership across year/month boundaries and on the cutoff date in text and JSON.
- F4.AC2: The cutoff must have the exact YYYY-MM-DD form and be a real calendar date; invalid values produce the exact original-value error, no stdout, and exit code 2.
  Source: "DATE is valid only when it has the form `YYYY-MM-DD` and names a real calendar date (`2024-02-30` and `2024-13-01` are invalid)."
  Source: "Otherwise print `error: invalid date '<value>'` to standard error, with `<value>` exactly as given, print nothing to standard output, and exit with code `2`."
  Aspect: `report FILE --since` receives a value outside exact `YYYY-MM-DD` form -> stderr equals `error: invalid date '<value>'` with `<value>` replaced by the unchanged argument and a newline
  Aspect: `report FILE --since 2024-02-30` is invoked -> stderr is `error: invalid date '2024-02-30'\n`
  Aspect: `report FILE --since 2024-13-01` is invoked -> stderr is `error: invalid date '2024-13-01'\n`
  Aspect: `report FILE --since` receives an invalid calendar date -> stdout is empty
  Aspect: `report FILE --since` receives an invalid calendar date -> the command exits with code `2`
  Example: `report FILE --since 2024-02-30` -> stderr `error: invalid date '2024-02-30'\n`, exit code `2`
  Example: `report FILE --since 2024-13-01` -> stderr `error: invalid date '2024-13-01'\n`, exit code `2`
  Verification: `tests/test_tally_part2.py::test_invalid_since` parametrizes the stated dates, non-leap February 29, invalid days/months, short fields, alternate separators, datetime suffixes, Unicode digits, whitespace and empty strings. `tests/test_tally_part2.py::test_valid_calendar_cutoffs` asserts successful reports for real dates including leap-day 2024-02-29.
- F4.AC3: Validate every row amount before applying its filter; a dropped row with an invalid amount still reports its original file line and exits with code 1.
  Source: "Every row's amount is validated (and reported with its file line number, as in part 1) before filtering, so a bad amount in a row that `--since` would drop is still an error, exit code `1`."
  Aspect: `report FILE --since 2024-03-01` reads line 3 dated before the cutoff with amount `abc` -> stderr is `error: line 3: invalid amount 'abc'\n`
  Aspect: `report FILE --since 2024-03-01` reads an invalid amount on a row before the cutoff -> the command exits with code `1`
  Aspect: `report FILE --since 2024-03-01` reads a retained valid row followed by a dropped invalid row -> stdout is empty
  Example: `report FILE --since 2024-03-01` with invalid amount `abc` on line 3 dated 2024-02-28 -> stderr `error: line 3: invalid amount 'abc'\n`, exit code `1`
  Verification: `tests/test_tally_part2.py::test_amount_validation_before_filter` covers dropped and retained invalid rows, first-invalid ordering and original line numbers in text and JSON; asserts exact stderr, empty stdout and status.
- F4.AC4: Filtering away all rows is a successful empty report in the selected format.
  Source: "If filtering leaves no rows, the empty-input rule applies: text output prints `no data`, JSON output prints `[]`, exit code `0`."
  Aspect: `report FILE --since 2024-03-01 --format text` reads only earlier valid rows -> stdout is `no data\n`
  Aspect: `report FILE --since 2024-03-01 --format json` reads only earlier valid rows -> stdout is `[]\n`
  Aspect: `report FILE --since 2024-03-01` reads only earlier valid rows in either format -> the command exits with code `0`
  Example: `report FILE --since 2024-03-01 --format json` with only a 2024-02-28 row -> stdout `[]\n`, exit code `0`
  Verification: `tests/test_tally_part2.py::test_filtered_empty_report` asserts exact stdout, status and empty stderr for default text, explicit text and JSON.

### F5 TALLY_TOP environment default

Outcome: A nonempty environment value supplies the existing top limit only when the command line omits top; explicit top always takes precedence.

Acceptance criteria:

- F5.AC1: Without explicit top, a nonempty environment value uses the accepted digits-only top validation and range 1 through 10.
  Source: "The environment variable `TALLY_TOP` sets the default for `--top`:"
  Source: "If `--top` is not given and `TALLY_TOP` is set to a non-empty value, that value is used as N, and it must be valid by the same rule as `--top` (only digits, value 1 to 10)."
  Aspect: `report FILE` without `--top` reads ten distinct categories with `TALLY_TOP` set to each accepted value from `1` to `10` -> stdout contains N category lines selected by the accepted report ordering
  Aspect: `report FILE --format json` reads ten categories with `TALLY_TOP=2` -> its JSON array contains exactly `2` category objects
  Example: `report FILE` with ten categories and `TALLY_TOP=1` -> `1` category line
  Example: `report FILE` with ten categories and `TALLY_TOP=10` -> `10` category lines
  Verification: `tests/test_tally_part2.py::test_environment_top_valid` parametrizes 1 through 10 and accepted leading-zero forms, including thousands of leading zeros, and checks exact selected output for text and JSON.
- F5.AC2: Any explicit top overrides the environment completely, including when the environment is invalid.
  Source: "If `--top` is given, it takes precedence and `TALLY_TOP` is ignored entirely, even when `TALLY_TOP` is invalid."
  Aspect: `report FILE --top 2` reads three categories with invalid `TALLY_TOP=abc` -> stdout contains exactly `2` category lines
  Aspect: `report FILE --top 0` is invoked with invalid `TALLY_TOP=abc` -> stderr is `error: --top must be between 1 and 10\n`
  Aspect: `report FILE --top abc --top 1` reads three categories with invalid `TALLY_TOP=abc` -> stdout contains exactly `1` category line
  Verification: `tests/test_tally_part2.py::test_explicit_top_overrides_environment` checks valid and invalid explicit final values against valid and invalid environments; asserts selected output or the explicit-top error.
- F5.AC3: Invalid environment top without explicit top returns its own exact error, no stdout, and exit code 2.
  Source: "If `--top` is not given and `TALLY_TOP` is invalid, print `error: TALLY_TOP must be between 1 and 10` to standard error, nothing to standard output, and exit with code `2`."
  Aspect: `report FILE` is invoked without `--top` and with invalid `TALLY_TOP` -> stderr is `error: TALLY_TOP must be between 1 and 10\n`
  Aspect: `report FILE` is invoked without `--top` and with invalid `TALLY_TOP` -> stdout is empty
  Aspect: `report FILE` is invoked without `--top` and with invalid `TALLY_TOP` -> the command exits with code `2`
  Example: `report FILE` with `TALLY_TOP=11` -> stderr `error: TALLY_TOP must be between 1 and 10\n`, exit code `2`
  Verification: `tests/test_tally_part2.py::test_environment_top_invalid` reuses the earlier top-invalid value cases except empty, including non-ASCII digits and huge numeric strings; asserts exact stderr, stdout and status.
- F5.AC4: Empty environment top is absent; without an explicit or nonempty environment top, the limit is 3.
  Source: "An empty `TALLY_TOP` counts as not set."
  Source: "If neither is given, N is `3`."
  Aspect: `report FILE` reads five categories with `TALLY_TOP` absent and no `--top` -> stdout contains exactly `3` category lines
  Aspect: `report FILE` reads five categories with `TALLY_TOP` empty and no `--top` -> stdout equals the report with `TALLY_TOP` absent
  Example: `report FILE` with five categories and empty `TALLY_TOP` -> `3` category lines
  Verification: `tests/test_tally_part2.py::test_environment_top_empty_or_absent` checks identical exact outputs with empty and absent environment values, in both formats.

### F6 Order of checks

Outcome: Only the first applicable error is reported in the required semantic order, independent of option order, with option checks complete before opening FILE.

Acceptance criteria:

- F6.AC1: Error precedence is resolved top, format, since, unreadable FILE, then invalid amounts; report only the first error.
  Source: "Option errors are reported before FILE is read, and only the first error is reported, in this order: the top value (`--top`, or `TALLY_TOP` when `--top` is absent), then `--format`, then `--since`, then FILE cannot be read, then invalid amounts."
  Source: "For example `--format xml --since nope` reports the `--format` error."
  Aspect: `report FILE --top 0 --format xml --since nope` names an unreadable FILE -> stderr is only `error: --top must be between 1 and 10\n`
  Aspect: `report FILE --format xml --since nope` names an unreadable FILE with invalid `TALLY_TOP=abc` and no `--top` -> stderr is only `error: TALLY_TOP must be between 1 and 10\n`
  Aspect: `report FILE --format xml --since nope` names an unreadable FILE with valid resolved top -> stderr is only `error: --format must be text or json\n`
  Aspect: `report FILE --since nope --format json --top 2` names an unreadable FILE -> stderr is only `error: invalid date 'nope'\n`
  Aspect: `report ./missing.csv --top 2 --format json --since 2024-03-01` names an unreadable FILE -> stderr is only `error: cannot read ./missing.csv\n`
  Aspect: `report FILE` with valid options reads a CSV containing multiple invalid amounts -> stderr reports only the first invalid amount in file order
  Aspect: `report FILE` with an invalid top, format or since is executed with monitored file opening -> FILE is opened `0` times
  Example: `report FILE --format xml --since nope` with valid resolved top -> stderr `error: --format must be text or json\n`
  Verification: `tests/test_tally_part2.py::test_check_precedence` parametrizes competing failures and reversed option order, asserting one exact stderr line, empty stdout and correct status. `tests/test_tally_part2.py::test_options_before_open` uses the existing monitored entry-point pattern to assert zero input opens for each option error.

### F7 Worked example

Outcome: The issue's complete sample demonstrates inclusive filtering, aggregation, alphabetical tie-breaking and top truncation together.

Acceptance criteria:

- F7.AC1: The supplied five-row sample and invocation produce the specified two-object JSON line and success status.
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
  Aspect: `report sample.csv --since 2024-03-01 --format json --top 2` reads the exact supplied CSV from `sample.csv` -> stdout is `[{"category": "food", "total": "19.75"}, {"category": "travel", "total": "19.75"}]\n`
  Aspect: `report sample.csv --since 2024-03-01 --format json --top 2` reads the supplied CSV -> the command exits with code `0`
  Aspect: `report sample.csv --since 2024-03-01 --format json --top 2` reads the supplied CSV -> the reported category sequence is `food`, `travel`
  Aspect: `report sample.csv --since 2024-03-01 --format json --top 2` reads the supplied CSV with `rent` dated before `2024-03-01` -> `rent` is absent from the array
  Aspect: `report sample.csv --since 2024-03-01 --format json --top 2` reads the supplied CSV including `food` dated exactly `2024-03-01` -> the reported `food` total is `19.75`
  Aspect: `report sample.csv --since 2024-03-01 --format json --top 2` reads the supplied CSV -> the reported `travel` total is `19.75`
  Aspect: `report sample.csv --since 2024-03-01 --format json --top 2` reads the supplied CSV with `books` totaling `4.50` and the `--top 2` limit -> `books` is absent from the array
  Example: `report sample.csv --since 2024-03-01 --format json --top 2` with the exact supplied CSV -> `[{"category": "food", "total": "19.75"}, {"category": "travel", "total": "19.75"}]\n`
  Verification: `tests/test_tally_part2.py::test_part2_worked_example` writes exactly the supplied sample, runs the command from its directory and asserts literal UTF-8 stdout, decoded category order, empty stderr and status.

## Decisions and assumptions

- Preserve all earlier tests; no test retirement is justified. Keep exact decimal arithmetic without binary floats, category text identity, descending total order with ascending Python string order for ties, UTF-8 byte output, amount grammar, original line numbering and accepted errors. (source: rung 1: `tests/test_tally.py:188`, `tests/test_tally.py:244`, `tests/test_tally.py:249`, `tests/test_tally.py:276`, `tests/test_tally.py:290`, `tests/test_tally.py:316`, `tests/test_tally.py:526`.)
- Keep the existing usage error text for unsupported invocations, including when none of the new controls is used; the extended valid syntax does not require replacing that protected error text. Continue rejecting unknown options, extra positional words and equals-style options. (source: rung 1: `tests/test_tally.py:558`, `tally.py:10`, `tally.py:16`.)
- Preserve the earlier deterministic standard-library runtime and no-network/no-clock tests, including their structural import assertions; choose implementation structure compatible with them rather than silently retiring them. The builder must accommodate the exact import assertions when adding JSON, environment and calendar functionality. (source: rung 1: `tests/test_tally.py:71`, `tests/test_tally.py:98`, `tests/test_tally.py:138`, `pyproject.toml:4`.)
- Reuse ASCII-only top validation, accepting leading zeros without converting unbounded digit strings first; environment top follows that same accepted grammar. Explicit top presence is tracked separately from its value so an invalid explicit value never falls back to the environment. (source: rung 1: `tally.py:20`, `tests/test_tally.py:368`, `tests/test_tally.py:414`; rung 2: "If `--top` is given, it takes precedence and `TALLY_TOP` is ignored entirely, even when `TALLY_TOP` is invalid.")
- Do not introduce validation of dates stored in FILE: accepted reports tolerate arbitrary date fields without since, and with since the issue calls for plain string comparison. Validate only the cutoff. (source: rung 1: `tests/test_tally.py:167`; rung 2: "Dates in the file and DATE are written `YYYY-MM-DD`, so they compare as plain strings.")
- Assumption: Exact cutoff form means ASCII digits with four-digit year, two-digit month/day and literal hyphens, with Gregorian dates in years 0001 through 9999. Reject year 0000 and Unicode digit variants; do not trim input. Reverse by changing cutoff validation and its boundary tests if the supported calendar is later expanded. (source: rung 2: "DATE is valid only when it has the form `YYYY-MM-DD` and names a real calendar date (`2024-02-30` and `2024-13-01` are invalid)."; rung 4: default Gregorian calendar validation for Python.)
- Treat a missing terminal value for a recognized option as the empty final value, matching existing missing-top handling; format and since then return their own validation errors. Option/value pairs are consumed positionally, so an option-looking word in a value slot is the value, not a new option. (source: rung 1: `tally.py:18`, `tests/test_tally.py:565`.)
- Keep existing CSV assumptions and parsing scope; no new dialect, quoted-field support or malformed-file policy is implied. Escape literal quote/backslash characters present in categories at JSON rendering. (source: rung 1: `tally.py:40`, `tally.py:43`; rung 2: "Strings are escaped as JSON requires (a `"` in a category becomes `\"`), but non-ASCII characters are written as they are, encoded as UTF-8, never as `\uXXXX` escapes.")
- Retain newline-terminated stderr messages, no partial stdout on errors, unreadable-file exit code 1, and first-invalid-amount reporting. Resolve and validate final option values before input opening. (source: rung 1: `tally.py:54`, `tally.py:59`, `tests/test_tally.py:470`, `tests/test_tally.py:526`; rung 2: "Option errors are reported before FILE is read, and only the first error is reported, in this order: the top value (`--top`, or `TALLY_TOP` when `--top` is absent), then `--format`, then `--since`, then FILE cannot be read, then invalid amounts.")
- Put added pytest cases in root `tests/test_tally_part2.py`, reuse command-line and monitored-entry-point conventions, and use subprocess timeouts of 10 seconds. Environment-specific subprocesses extend the inherited environment and override only relevant keys; baseline cases explicitly remove TALLY_TOP to avoid host contamination. Run all earlier and new tests with changed-line coverage, ruff and strict mypy as required by the gate. (source: rung 1: `tests/test_tally.py:26`, `tests/test_tally.py:37`, `tests/test_tally.py:294`, `AGENTS.md:19`, `AGENTS.md:26`, `.git/hooks/pre-commit:15`.)
- This intake writes only this definition. No code/test edits, branch, push, commit or gate invocation are part of intake; the builder owns implementation and automated verification. (source: rung 2: "This is a fixture workspace for a benchmark task: there is no branch, no remote
  and no pull request, so do not branch or push anything."; intake's explicit one-file restriction.)
