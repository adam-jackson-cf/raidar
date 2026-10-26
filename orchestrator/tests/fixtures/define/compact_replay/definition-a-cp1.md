# Deterministic transaction summaries

## Objective

Build `tally.py`, a Python 3.12 standard-library command-line tool that reads a UTF-8 transactions CSV, computes exact category totals, and prints a deterministic ranked report. Validate the final top limit before reading the file, and report read or amount errors without partial report output. Execution uses neither the network nor the clock.

## Features

### F1 tally report command and runtime

Outcome: Users invoke the local Python command to summarize transactions without third-party runtime dependencies, network access, or clock access.

Acceptance criteria:
- F1.AC1: The Python 3.12 command accepts the report operation and a CSV path, with an optional top limit.
  Source: "Build a command-line tool, written in Python 3.12 using only the standard library, that summarises a CSV file of transactions by category."
  Source: "You run it as:"
  Source: "python tally.py report FILE [--top N]"
  Source: "`FILE` is the path of a CSV file."
  Aspect: `python tally.py report FILE` receives a CSV path containing a transaction in category `food` with amount `4.5` -> stdout is `food: 4.50\n`
  Aspect: `python tally.py report FILE --top N` receives a CSV path and `N` of `1` with two categories -> stdout contains exactly `1` report line
  Aspect: the report executes under Python `3.12` with only standard-library runtime imports available -> the command exits with code `0`
  Verification: `tests/test_tally.py::test_report_invocation` exercises both invocations in Python 3.12 subprocesses and asserts their reports; `tests/test_tally.py::test_standard_library_runtime` checks production imports against the standard library and runs the report without third-party runtime packages.
- F1.AC2: Running the report does not access the network or the clock.
  Source: "It must not use the network or the clock."
  Aspect: the report runs on a local CSV while network entry points are monitored -> the observed network-access count is `0`
  Aspect: the report runs on a local CSV while clock entry points are monitored -> the observed clock-access count is `0`
  Verification: `tests/test_tally.py::test_no_network_or_clock` executes the command entry point with monitored standard-library network and clock APIs, asserts zero accesses, and asserts the report output; inspect production imports and calls for additional access paths.

### F2 Input: FILE and transaction rows

Outcome: The command consumes the promised UTF-8 CSV layout and treats dates as uninterpreted transaction fields.

Acceptance criteria:
- F2.AC1: Read UTF-8 rows after the first-line header in date, category, amount order, using the issue's unquoted-field input contract.
  Source: "FILE is UTF-8."
  Source: "The first line is always the header `date,category,amount`, and it is line 1."
  Source: "Every other line is one transaction with exactly three comma-separated fields in the same order."
  Source: "Fields are never quoted, a category is never empty and never contains a comma, and the file contains no blank lines."
  Aspect: `python tally.py report FILE` reads a UTF-8 file with header `date,category,amount` and row `2024-01-03,café,4.5` -> stdout decoded as UTF-8 is `café: 4.50\n`
  Aspect: `python tally.py report FILE` reads the header on line `1` followed by unquoted rows `2024-01-03,café,4.5` and `2024-01-04,café,12.00` with no blank lines -> stdout is `café: 16.50\n`
  Example: a header `date,category,amount` followed by `2024-01-03,café,4.5` -> `café: 4.50\n`
  Verification: `tests/test_tally.py::test_utf8_csv_layout` writes UTF-8 fixture bytes, invokes the CLI, and compares the complete decoded report, including multirow totals and exclusion of the header.
- F2.AC2: Dates are not validated.
  Source: "Dates are not checked at this stage."
  Aspect: `python tally.py report FILE` reads row `not-a-date,food,4.5` after the header -> stdout is `food: 4.50\n`
  Verification: `tests/test_tally.py::test_dates_not_checked` runs the CLI with a non-date first field and asserts the successful report.

### F3 Amount validation and exact decimal arithmetic

Outcome: The command accepts the stated decimal amount grammar and preserves exact values when summing transactions.

Acceptance criteria:
- F3.AC1: Accept amounts consisting of one or more digits and optionally a decimal point followed by one or two digits, including leading zeros; reject the stated invalid forms.
  Source: "`amount` is valid when it is one or more digits, optionally followed by a point and then one or two digits."
  Source: "So `4.5`, `12.00`, `800` and `007.5` are valid (leading zeros are allowed and `007.5` is the number 7.5), while `1.234`, `abc`, `-3`, `.5`, `4.` and an amount with spaces around it are invalid."
  Aspect: `python tally.py report FILE` reads a transaction with `amount` text `4.5`, `12.00`, `800`, or `007.5` -> the command exits with code `0`
  Aspect: `python tally.py report FILE` reads a transaction with `amount` text `1.234`, `abc`, `-3`, `.5`, `4.`, or ` 4.5 ` -> the command exits with code `1`
  Example: one `food` transaction with amount `4.5` -> `food: 4.50\n`
  Example: one `food` transaction with amount `12.00` -> `food: 12.00\n`
  Example: one `food` transaction with amount `800` -> `food: 800.00\n`
  Example: one `food` transaction with amount `007.5` -> `food: 7.50\n`
  Verification: `tests/test_tally.py::test_amount_grammar` parametrizes each accepted and rejected text, runs the CLI, and asserts its exit status and complete output or error; `tests/test_tally.py::test_leading_zero_amount` asserts the normalized decimal value.
- F3.AC2: Sum amounts as exact decimal values without binary floating point.
  Source: "Amounts are exact decimal numbers."
  Source: "Add them as decimals, never as binary floating point: `0.10` plus `0.20` is exactly `0.30`."
  Aspect: `python tally.py report FILE` reads `food` transactions with amounts `0.10` and `0.20` -> stdout is `food: 0.30\n`
  Aspect: production amount conversion and summation used by the report are inspected -> binary-floating-point conversions and arithmetic are absent from that path
  Example: category `food` with amounts `0.10` plus `0.20` -> total `0.30`
  Verification: `tests/test_tally.py::test_exact_decimal_sum` asserts the exact CLI output, including large decimal amounts that would expose rounding; `tests/test_tally.py::test_no_binary_float_amounts` checks the production amount path for floating-point conversion or arithmetic.

### F4 Output: grouping, formatting, and ordering

Outcome: Successful reports preserve category names, print two-decimal totals in UTF-8, and rank categories by decreasing total with deterministic name ordering for ties.

Acceptance criteria:
- F4.AC1: Aggregate transactions by the exact category text, preserving case and spaces.
  Source: "Group the transactions by category and sum the amounts of each category."
  Source: "A category name is used exactly as written: it is case-sensitive, and spaces are part of the name, so `food` and ` food` (leading space) are two different categories, and a space is printed as written."
  Aspect: `python tally.py report FILE` reads two transactions in category `food` with amounts `12.50` and `7.25` -> stdout contains the line `food: 19.75\n`
  Aspect: `python tally.py report FILE` reads equal-amount transactions in categories `food` and ` food` -> stdout is ` food: 1.00\nfood: 1.00\n`
  Aspect: `python tally.py report FILE` reads equal-amount transactions in categories `Food` and `food` -> stdout is `Food: 1.00\nfood: 1.00\n`
  Example: equal totals for `food` and ` food` -> distinct report lines ` food: 1.00\nfood: 1.00\n`
  Verification: `tests/test_tally.py::test_category_identity` asserts complete CLI reports for repeated names, leading spaces, and different case.
- F4.AC2: Print each selected category as a UTF-8 line with exactly two fractional digits and a terminating newline; successful reports have no other stdout, empty stderr, and exit code zero.
  Source: "Print one line per category to standard output (encoded as UTF-8):"
  Source: "<category>: <total>"
  Source: "The total always has exactly two digits after the point (`800` is printed as `800.00`, `007.5` as `7.50`)."
  Source: "Each line ends with a newline."
  Source: "Nothing else is written to standard output, and standard error stays empty."
  Source: "The exit code is `0`."
  Aspect: `python tally.py report FILE` reads category `café` with amount `800` -> stdout bytes equal `café: 800.00\n` encoded as UTF-8
  Aspect: `python tally.py report FILE` reads category `food` with amount `007.5` -> stdout is `food: 7.50\n`
  Aspect: `python tally.py report FILE` reads category `food` with amount `800` -> the printed total is `800.00`
  Aspect: `python tally.py report FILE` reads category `food` with amount `007.5` -> the printed total is `7.50`
  Aspect: `python tally.py report FILE` successfully reports a valid nonempty input -> stderr is empty
  Aspect: `python tally.py report FILE` successfully reports a valid nonempty input -> the command exits with code `0`
  Example: `<category>` of `food` and `<total>` from amount `800` -> `food: 800.00\n`
  Example: `<category>` of `food` and `<total>` from amount `007.5` -> `food: 7.50\n`
  Verification: `tests/test_tally.py::test_output_contract` captures raw CLI streams and asserts exact UTF-8 bytes, final newlines, stderr, and exit code.
- F4.AC3: Sort category totals descending, breaking ties by ascending plain-string code-point order.
  Source: "Order the lines by total, highest first."
  Source: "Categories with equal totals are ordered by category name in ascending code-point order (plain string comparison, so `a` comes before `b`, and `B` before `a`)."
  Aspect: `python tally.py report FILE --top 10` reads transactions `b` with amount `1`, `a` with amount `10`, and `c` with amount `2` -> stdout is `a: 10.00\nc: 2.00\nb: 1.00\n`
  Aspect: `python tally.py report FILE` reads equal totals for categories `b`, `a`, and `B` -> stdout is `B: 1.00\na: 1.00\nb: 1.00\n`
  Example: equal totals for categories `b`, `a`, and `B` -> category order `B`, `a`, `b`
  Verification: `tests/test_tally.py::test_report_ordering` asserts complete reports for distinct numeric totals and equal-total ASCII and Unicode names using code-point ordering.

### F5 --top N

Outcome: The optional limit defaults to three, uses only its final occurrence, and rejects invalid final limits before file access.

Acceptance criteria:
- F5.AC1: The option is supplied as two separate words, and only the last occurrence is used and validated.
  Source: "`--top` is optional and is written as two separate words, `--top` then `N`."
  Source: "If `--top` is given more than once, only the last one is used (and only that one is checked)."
  Aspect: `python tally.py report FILE --top N` is invoked with `N` of `1` and two categories -> stdout contains exactly `1` report line
  Aspect: `python tally.py report FILE --top abc --top 2` reads three categories -> stdout contains exactly `2` ordered report lines
  Aspect: `python tally.py report FILE --top 2 --top abc` reads three categories -> stderr is `error: --top must be between 1 and 10\n`
  Verification: `tests/test_tally.py::test_repeated_top` asserts the ordered output when an earlier invalid value is superseded and the error when the final value is invalid; `tests/test_tally.py::test_top_separate_words` exercises the documented option syntax.
- F5.AC2: Print the first N ordered lines, using three by default and printing every category when fewer than N exist.
  Source: "`--top N` prints only the first `N` lines of the ordered output."
  Source: "The default is `3`."
  Source: "If there are fewer than `N` categories, all of them are printed."
  Aspect: `python tally.py report FILE` with option `--top N` reads five categories with `N` of `2` -> stdout equals the first `2` lines of the full ordered report
  Aspect: `python tally.py report FILE` reads five categories without `--top` -> stdout equals the first `3` lines of the full ordered report
  Aspect: `python tally.py report FILE --top N` reads two categories with `N` of `10` -> stdout equals both ordered category lines
  Example: five categories reported without `--top` -> report-line count `3`
  Verification: `tests/test_tally.py::test_top_selection` asserts exact report contents for explicit, default, and larger-than-category-count limits.
- F5.AC3: The final N consists only of ASCII digits and represents an integer from one through ten; invalid N produces the specified error, no stdout, and exit code two before reading FILE.
  Source: "`N` must consist only of the digits `0` to `9` and have a value from `1` to `10` inclusive."
  Source: "Otherwise the tool prints `error: --top must be between 1 and 10` to standard error, prints nothing to standard output, and exits with code `2`."
  Source: "This is checked before FILE is read, so an invalid `--top` is reported even when FILE does not exist."
  Aspect: `python tally.py report FILE --top N` reads sufficient categories with each `N` from `1` through `10` inclusive -> stdout has exactly `N` ordered report lines
  Aspect: `python tally.py report FILE --top N` receives `N` of `0`, `11`, `-1`, `+1`, `1.0`, `abc`, an empty string, surrounding whitespace, or non-ASCII digits -> stderr is `error: --top must be between 1 and 10\n`
  Aspect: `python tally.py report FILE --top N` receives an invalid final `N` -> stdout is empty
  Aspect: `python tally.py report FILE --top N` receives an invalid final `N` -> the command exits with code `2`
  Aspect: `python tally.py report FILE --top 0` names a nonexistent `FILE` -> stderr is `error: --top must be between 1 and 10\n`
  Aspect: the report entry point receives invalid `--top` while file reads are monitored -> file-read count is `0`
  Example: `--top 0` with a nonexistent file -> stderr `error: --top must be between 1 and 10\n`, exit code `2`
  Example: valid `--top 1` over ten categories -> report-line count `1`
  Example: valid `--top 9` over ten categories -> report-line count `9`
  Example: valid `--top 10` over ten categories -> report-line count `10`
  Verification: `tests/test_tally.py::test_top_validation` parametrizes every valid boundary and invalid class, asserts exact streams and exit status, and includes nonexistent-file precedence; `tests/test_tally.py::test_top_before_file_read` monitors reads at the command entry point.

### F6 Errors: unreadable FILE and invalid amounts

Outcome: Read failures and the first invalid amount produce deterministic stderr errors, exit code one, and no partial stdout.

Acceptance criteria:
- F6.AC1: For a file that cannot be read, preserve the supplied path in the error and emit no report.
  Source: "If FILE cannot be read (it does not exist, or it is a directory, or anything else prevents reading it), print `error: cannot read <FILE>` to standard error, with `<FILE>` exactly as given on the command line."
  Source: "Print nothing to standard output and exit with code `1`."
  Aspect: `python tally.py report FILE` names nonexistent path `./missing.csv` -> stderr is `error: cannot read ./missing.csv\n`
  Aspect: `python tally.py report FILE` names a directory `./transactions` -> stderr is `error: cannot read ./transactions\n`
  Aspect: the report entry point receives `FILE` of `./denied.csv` and reading raises a permission error -> stderr is `error: cannot read ./denied.csv\n`
  Aspect: `python tally.py report FILE` cannot read the supplied `<FILE>` -> stdout is empty
  Aspect: `python tally.py report FILE` cannot read the supplied `<FILE>` -> the command exits with code `1`
  Example: nonexistent `FILE` supplied as `./missing.csv` -> `error: cannot read ./missing.csv\n`
  Verification: `tests/test_tally.py::test_unreadable_file` runs missing-path and directory CLI cases and asserts exact streams and status; `tests/test_tally.py::test_read_failure` injects permission and mid-read failures at the entry point and asserts the same contract without relying on host permissions.
- F6.AC2: Stop at the first invalid amount, reporting its one-based file line number and exact amount text; emit no report even after valid preceding rows.
  Source: "If any row has an invalid amount, print `error: line <n>: invalid amount '<value>'` to standard error, where `<n>` is the 1-based line number in the file of the first such row (the header is line 1) and `<value>` is the amount text exactly as written."
  Source: "Print nothing at all to standard output, even if earlier rows were fine, and exit with code `1`."
  Aspect: `python tally.py report FILE` reads a header on line `1`, a valid row on line `2`, invalid amount ` 4.5 ` on line `3`, and another invalid row on line `4` -> stderr is `error: line 3: invalid amount ' 4.5 '\n`
  Aspect: `python tally.py report FILE` reads an invalid amount `abc` immediately after the header -> stderr is `error: line 2: invalid amount 'abc'\n`
  Aspect: `python tally.py report FILE` reads valid rows followed by an invalid amount -> stdout is empty
  Aspect: `python tally.py report FILE` reads any invalid amount -> the command exits with code `1`
  Example: first invalid `<value>` is `abc` at `<n>` of `2` -> `error: line 2: invalid amount 'abc'\n`
  Verification: `tests/test_tally.py::test_first_invalid_amount` asserts exact line numbers and text for first-row, later-row, multiple-invalid-row, and whitespace-preservation cases, plus empty stdout and status.

### F7 Empty input

Outcome: A header-only file yields a successful no-data message.

Acceptance criteria:
- F7.AC1: A file containing only the header prints the newline-terminated no-data message and succeeds.
  Source: "If FILE contains only the header line, print `no data` followed by a newline to standard output and exit with code `0`."
  Source: "Nothing else is written to standard output, and standard error stays empty."
  Aspect: `python tally.py report FILE` reads only `date,category,amount` -> stdout is `no data\n`
  Aspect: `python tally.py report FILE` reads only the header -> the command exits with code `0`
  Aspect: `python tally.py report FILE` reads only the header -> stderr is empty
  Example: header-only FILE -> `no data\n`
  Verification: `tests/test_tally.py::test_header_only` asserts all three CLI observables with both the default and a valid explicit top limit.

### F8 Worked example: sample.csv

Outcome: The issue's sample produces exactly its stated three-line report.

Acceptance criteria:
- F8.AC1: Report the supplied sample with the stated totals, tie ordering, and default truncation.
  Source: "Given `sample.csv`:"
  Source: "Running `report sample.csv` prints (exit code `0`):"
  Source: "rent: 800.00"
  Source: "books: 19.75"
  Source: "food: 19.75"
  Source: "`food` totals `19.75`, the same as `books` and `travel`, so those three are ordered by name; `rent` is first because `800` is the largest total."
  Source: "Only three lines are printed because the default for `--top` is `3`, so `travel` is cut."
  Aspect: `python tally.py report sample.csv` (the `report sample.csv` operation) reads the supplied five transactions in categories `food`, `books`, `travel`, and `rent`, including `rent` amount `800` -> stdout is `rent: 800.00\nbooks: 19.75\nfood: 19.75\n`
  Aspect: `python tally.py report sample.csv` reads the supplied five transactions -> the printed `food` total is `19.75`
  Aspect: `python tally.py report sample.csv` reads the supplied five transactions without `--top` -> the command exits with code `0`
  Aspect: `python tally.py report sample.csv` reads the supplied five transactions without `--top` -> the report contains exactly `3` lines
  Example: `sample.csv` with rows `2024-01-03,food,12.50`, `2024-01-05,rent,800`, `2024-01-09,food,7.25`, `2024-01-11,books,19.75`, and `2024-01-12,travel,19.75` after the header -> `rent: 800.00\nbooks: 19.75\nfood: 19.75\n`
  Verification: `tests/test_tally.py::test_worked_example` recreates the sample verbatim and asserts complete streams, report-line count, and exit code for the default report.

## Decisions and assumptions

- Preserve the existing project configuration and satisfy ruff, strict mypy, source and structure checks, and full changed-production-line test coverage. Do not weaken checks or add coverage exclusions. (source: rung 1: `AGENTS.md:12`, `AGENTS.md:14`, `AGENTS.md:20`, `AGENTS.md:23`, `AGENTS.md:34`, `AGENTS.md:68`; `pyproject.toml:4` and `pyproject.toml:5` establish the compatible Python version and empty dependencies.)
- Put automated tests in root-level `tests/test_tally.py`; use pytest collection, actual report/error assertions, and CLI subprocesses with `timeout=10` and an inherited environment. Use entry-point execution for controlled read failures and monitoring. (source: rung 1: `AGENTS.md:20`, `AGENTS.md:25`, `AGENTS.md:30`, `AGENTS.md:32`, `AGENTS.md:72`.)
- Greenfield inventory contains no production code, test modules, README, or CLAUDE.md; there is no earlier tested behavior to preserve or retire. The builder creates root-level `tally.py`. (source: rung 1: repository file inventory; rung 2: "python tally.py report FILE [--top N]". File placement follows the documented invocation; reverse by changing the invocation requirement.)
- Build only a local workspace artifact; do not branch, push, or create a pull request. (source: rung 2: "This is a fixture workspace for a benchmark task: there is no branch, no remote and no pull request, so do not branch or push anything.")
- Treat the header, field count, unquoted fields, nonempty comma-free categories, and absence of blank lines as input guarantees, not requests for additional validators. Ignore date contents. Accept ordinary LF and CRLF line endings without retaining the record terminator in amount text. (source: rung 2: "The first line is always the header `date,category,amount`, and it is line 1.", "Every other line is one transaction with exactly three comma-separated fields in the same order.", "Fields are never quoted, a category is never empty and never contains a comma, and the file contains no blank lines.", "Dates are not checked at this stage."; rung 3: standard-library text-file universal-newline behavior. The newline choice is an assumption; reverse by requiring a particular record terminator.)
- Interpret amount digits as ASCII `0` through `9`, with a full-string grammar equivalent to `[0-9]+(?:\.[0-9]{1,2})?`. Do not strip amount whitespace. (source: rung 4: default numeric interchange grammar; rung 2: "So `4.5`, `12.00`, `800` and `007.5` are valid (leading zeros are allowed and `007.5` is the number 7.5), while `1.234`, `abc`, `-3`, `.5`, `4.` and an amount with spaces around it are invalid.". ASCII-only amount digits are an assumption; reverse by explicitly defining Unicode-digit acceptance.)
- Use standard-library `decimal.Decimal` for amount conversion and summation, with precision sized to the input so valid large values and their sums are not rounded by the default decimal context. Do not introduce an unstated amount-size limit. (source: rung 2: "Amounts are exact decimal numbers.", "Add them as decimals, never as binary floating point: `0.10` plus `0.20` is exactly `0.30`."; rung 4: default exact-decimal implementation. The implementation choice is an assumption; reverse by selecting another exact-decimal representation that preserves all stated observables.)
- Keep original category text, aggregate all rows before writing a report, sort by decreasing exact total then ascending original category string, and apply the selected top limit after sorting. The no-data message takes precedence over category output for a header-only file after valid option validation. (source: rung 2: "Group the transactions by category and sum the amounts of each category.", "Order the lines by total, highest first.", "`--top N` prints only the first `N` lines of the ordered output.", "Print nothing at all to standard output, even if earlier rows were fine, and exit with code `1`.", "If FILE contains only the header line, print `no data` followed by a newline to standard output and exit with code `0`.". Buffering is an implementation assumption; reverse by another strategy that guarantees identical output and error atomicity.)
- Accept leading zeros in N, such as `01` and `00010`, because they contain only ASCII digits and their numeric values are within the stated range. Validate only the final supplied top value and do not set a digit-count limit; determine its value without depending on a runtime integer-string length limit. (source: rung 2: "`N` must consist only of the digits `0` to `9` and have a value from `1` to `10` inclusive.", "If `--top` is given more than once, only the last one is used (and only that one is checked).". This interpretation is an assumption; reverse by explicitly forbidding leading zeros or imposing a digit-count limit.)
- Reject unsupported invocation forms, including `--top=N`, unknown commands/options, missing FILE, or extra positional arguments, with empty stdout, an explanatory newline-terminated stderr message, and exit code `2`. A final `--top` without a value uses the specified top-validation error. These unspecified syntax details do not require additional feature outcomes. (source: rung 2: "`--top` is optional and is written as two separate words, `--top` then `N`."; rung 4: default command-line usage-error convention. This is an assumption; reverse by changing the supported invocation grammar or usage-error contract.)
- Preserve the original FILE argument string for read errors. Treat open, decoding, and mid-read failures as cannot-read errors, with no traceback or partial report. Use one terminating newline for error messages. (source: rung 2: "If FILE cannot be read (it does not exist, or it is a directory, or anything else prevents reading it), print `error: cannot read <FILE>` to standard error, with `<FILE>` exactly as given on the command line."; rung 4: default concise CLI error convention. Decoding/mid-read classification and terminating newlines are assumptions; reverse by defining separate error contracts for them.)
- Ensure stdout uses UTF-8 independently of locale, with LF bytes terminating report and no-data lines. Do not strip or normalize category names, including Unicode names. (source: rung 2: "Print one line per category to standard output (encoded as UTF-8):", "Each line ends with a newline.", "A category name is used exactly as written: it is case-sensitive, and spaces are part of the name, so `food` and ` food` (leading space) are two different categories, and a space is printed as written."; rung 5: default LF byte representation. Explicit LF bytes are an assumption; reverse by specifying platform-native newline translation.)
