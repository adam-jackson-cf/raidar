# Exact transaction totals from a CSV command

## Objective

Build `tally.py`, a Python 3.12 standard-library command-line tool that reads a transaction CSV, sums exact decimal amounts by unchanged category names, sorts the totals, and prints a configurable number of results. Make success, empty input, and errors deterministic without network or clock access.

## Features

### F1 tally / report command and runtime

Outcome: Users run a local Python command to summarise transactions without external dependencies, network access, or clock access.

Acceptance criteria:
- F1.AC1: Provide the Python 3.12 standard-library entry point `python tally.py report FILE [--top N]` for reporting a transaction CSV by category.
  Source: "Build a command-line tool, written in Python 3.12 using only the standard library, that summarises a CSV file of transactions by category."
  Source: "You run it as:"
  Source: "python tally.py report FILE [--top N]"
  Aspect: the documented entry point `python tally.py report FILE [--top N]` runs under Python `3.12` with the optional words omitted on a CSV containing a transaction `food,4.5` after its date field -> stdout is `food: 4.50\n`
  Aspect: `python tally.py report FILE --top N` runs with `N` equal to `1` on a CSV containing one category -> stdout contains exactly `1` category line
  Aspect: the delivered `tally.py` command and its runtime imports are inspected -> all runtime dependencies belong to the Python `3.12` standard library
  Example: `python tally.py report sample.csv --top 1` on the worked example -> stdout `rent: 800.00\n`
  Verification: `tests/test_cli.py::test_report_entry_point` invokes the actual script on fixtures under Python 3.12; `tests/test_runtime.py::test_standard_library_runtime` checks runtime imports against the standard library.
- F1.AC2: The command never uses the network or reads the clock.
  Source: "It must not use the network or the clock."
  Aspect: the `report` command processes a valid CSV with network operations guarded against access -> network access attempts equal `0`
  Aspect: the `report` command processes a valid CSV with clock-reading operations guarded against access -> clock access attempts equal `0`
  Verification: `tests/test_runtime.py::test_no_network_or_clock` runs the command with guards for network and wall/monotonic clock reads and asserts no access attempts; review the runtime import/call surface for unguarded alternatives.

### F2 FILE / Input and amount parsing

Outcome: The command reads the specified UTF-8 transaction file under the issue's format guarantees and interprets valid amounts exactly.

Acceptance criteria:
- F2.AC1: Treat FILE as a UTF-8 CSV path whose first line is the header and whose remaining lines contain date, category, and amount in that order; do not validate dates.
  Source: "`FILE` is the path of a CSV file."
  Source: "FILE is UTF-8."
  Source: "The first line is always the header `date,category,amount`, and it is line 1."
  Source: "Every other line is one transaction with exactly three comma-separated fields in the same order."
  Source: "Fields are never quoted, a category is never empty and never contains a comma, and the file contains no blank lines."
  Source: "Dates are not checked at this stage."
  Aspect: `python tally.py report FILE` uses `FILE` as a UTF-8 CSV path beginning with `date,category,amount` and a row `not-a-date,café,4.50` -> stdout is `café: 4.50\n`
  Aspect: `python tally.py report FILE` reads `date,category,amount` followed by `arbitrary,food,1.00` and `arbitrary,rent,2.00`, with no blank lines -> stdout is `rent: 2.00\nfood: 1.00\n`
  Example: FILE contains `date,category,amount\nnot-a-date,café,4.50\n` encoded as UTF-8 -> stdout `café: 4.50\n`
  Verification: `tests/test_input.py::test_utf8_fields_and_unchecked_dates` invokes the CLI and asserts decoded output for Unicode categories and arbitrary date text; `tests/test_input.py::test_header_is_not_a_transaction` proves the header is skipped.
- F2.AC2: Accept the stated integer and fractional amount forms, including leading zeros, and reject the stated invalid forms through F6.AC2.
  Source: "`amount` is valid when it is one or more digits, optionally followed by a point and then one or two digits."
  Source: "So `4.5`, `12.00`, `800` and `007.5` are valid (leading zeros are allowed and `007.5` is the number 7.5), while `1.234`, `abc`, `-3`, `.5`, `4.` and an amount with spaces around it are invalid."
  Aspect: `python tally.py report FILE` reads a `food` transaction with `amount` text `4.5` -> stdout is `food: 4.50\n`
  Aspect: `python tally.py report FILE` reads a `food` transaction with amount `12.00` -> stdout is `food: 12.00\n`
  Aspect: `python tally.py report FILE` reads a `food` transaction with amount `800` -> stdout is `food: 800.00\n`
  Aspect: `python tally.py report FILE` reads a `food` transaction with amount `007.5` -> stdout is `food: 7.50\n`
  Aspect: `python tally.py report FILE` reads a transaction with any of `1.234`, `abc`, `-3`, `.5`, `4.`, or ` 4.5 ` as amount -> the command exits with code `1`
  Example: one `food` transaction with amount `4.5` -> stdout `food: 4.50\n`
  Example: one `food` transaction with amount `12.00` -> stdout `food: 12.00\n`
  Example: one `food` transaction with amount `800` -> stdout `food: 800.00\n`
  Example: one `food` transaction with amount `007.5` -> stdout `food: 7.50\n`
  Example: a line-2 transaction with amount `1.234` -> stderr `error: line 2: invalid amount '1.234'\n`
  Example: a line-2 transaction with amount `abc` -> stderr `error: line 2: invalid amount 'abc'\n`
  Example: a line-2 transaction with amount `-3` -> stderr `error: line 2: invalid amount '-3'\n`
  Example: a line-2 transaction with amount `.5` -> stderr `error: line 2: invalid amount '.5'\n`
  Example: a line-2 transaction with amount `4.` -> stderr `error: line 2: invalid amount '4.'\n`
  Verification: `tests/test_amounts.py::test_valid_amount_forms` and `tests/test_amounts.py::test_invalid_amount_forms` parameterise these CLI inputs and assert output or rejection literally.
- F2.AC3: Perform exact decimal addition without binary floating-point rounding.
  Source: "Amounts are exact decimal numbers."
  Source: "Add them as decimals, never as binary floating point: `0.10` plus `0.20` is exactly `0.30`."
  Aspect: `python tally.py report FILE` reads amounts `0.10` and `0.20` for category `food` -> stdout is `food: 0.30\n`
  Example: `food` transactions with amounts `0.10` and `0.20` -> total `0.30`
  Verification: `tests/test_amounts.py::test_exact_decimal_addition` asserts the CLI result; `tests/test_amounts.py::test_large_exact_totals` checks that long valid amounts and repeated addition do not lose cents.

### F3 Output / category totals and formatting

Outcome: Output contains only category totals, preserves category text, and uses UTF-8 and two fractional digits.

Acceptance criteria:
- F3.AC1: Sum transactions by their exact case-sensitive category names, preserving spaces in names and output.
  Source: "Group the transactions by category and sum the amounts of each category."
  Source: "A category name is used exactly as written: it is case-sensitive, and spaces are part of the name, so `food` and ` food` (leading space) are two different categories, and a space is printed as written."
  Aspect: `python tally.py report FILE --top 10` reads two `food` transactions of `1.00` and `2.00` -> stdout contains the category line `food: 3.00\n`
  Aspect: `python tally.py report FILE --top 10` reads `food`, `Food`, and ` food` transactions of `1.00` each -> stdout is ` food: 1.00\nFood: 1.00\nfood: 1.00\n`
  Example: categories `food` and ` food` each have total `1.00` -> stdout ` food: 1.00\nfood: 1.00\n`
  Verification: `tests/test_output.py::test_category_identity` invokes the CLI with repeated, case-distinct, and space-distinct categories and asserts the exact category lines and totals.
- F3.AC2: Print selected categories as `<category>: <total>` in UTF-8, with two digits after the point and a newline after every line; successful nonempty output has no extra stdout, empty stderr, and exit code 0.
  Source: "Print one line per category to standard output (encoded as UTF-8):"
  Source: "<category>: <total>"
  Source: "The total always has exactly two digits after the point (`800` is printed as `800.00`, `007.5` as `7.50`)."
  Source: "Each line ends with a newline."
  Source: "Nothing else is written to standard output, and standard error stays empty."
  Source: "The exit code is `0`."
  Aspect: `python tally.py report FILE` reads a `café` transaction with amount `800` -> stdout bytes equal UTF-8 encoding of `café: 800.00\n`, using format `<category>: <total>`
  Aspect: `python tally.py report FILE` reads a `food` transaction with amount `007.5` -> stdout is `food: 7.50\n`
  Aspect: `python tally.py report FILE` successfully reads a nonempty valid CSV -> stderr is empty
  Aspect: `python tally.py report FILE` successfully reads a nonempty valid CSV -> the command exits with code `0`
  Example: category `food` with amount `800` -> stdout `food: 800.00\n`
  Example: category `food` with amount `007.5` -> stdout `food: 7.50\n`
  Example: amount `800` -> rendered total `800.00`
  Example: amount `007.5` -> rendered total `7.50`
  Verification: `tests/test_output.py::test_exact_output_bytes` checks complete stdout bytes, including the final newline and absence of extra text; `tests/test_output.py::test_success_streams_and_status` asserts empty stderr and status 0.

### F4 Output ordering

Outcome: Categories appear by descending total with deterministic ascending code-point ties.

Acceptance criteria:
- F4.AC1: Sort totals highest first and equal-total category names by plain ascending string comparison.
  Source: "Order the lines by total, highest first."
  Source: "Categories with equal totals are ordered by category name in ascending code-point order (plain string comparison, so `a` comes before `b`, and `B` before `a`)."
  Aspect: `python tally.py report FILE --top 10` reads categories with totals `2.00`, `10.00`, and `9.00` -> category order follows totals `10.00`, `9.00`, `2.00`
  Aspect: `python tally.py report FILE --top 10` reads equal-total categories `b`, `a`, and `B` in that input order -> stdout is `B: 1.00\na: 1.00\nb: 1.00\n`
  Example: equal totals for `b`, `a`, and `B` -> category order `B`, `a`, `b`
  Verification: `tests/test_ordering.py::test_descending_numeric_totals` distinguishes numeric sorting from lexical sorting; `tests/test_ordering.py::test_code_point_ties` asserts exact order across uppercase, lowercase, spaces, and Unicode names.

### F5 --top N

Outcome: Users select up to ten ordered category lines; omission selects three, repeated options use only the final value, and invalid final values fail before file access.

Acceptance criteria:
- F5.AC1: Support an optional two-word `--top N` option, not a joined spelling.
  Source: "`--top` is optional and is written as two separate words, `--top` then `N`."
  Aspect: `python tally.py report FILE` uses the optional `--top N` as separate words `--top` then `N`, with `N` equal to `1` and a nonempty valid CSV -> stdout contains exactly `1` category line
  Aspect: `python tally.py report FILE` omits `--top` on a CSV with four categories -> stdout contains exactly `3` category lines
  Example: command arguments `report sample.csv --top 1` -> stdout `rent: 800.00\n`
  Verification: `tests/test_top.py::test_optional_two_word_option` invokes the CLI with and without the separate option; `tests/test_top.py::test_joined_top_is_rejected` checks the two-word grammar.
- F5.AC2: Print the first N lines of the sorted result, defaulting to 3, and print all categories when fewer than N exist.
  Source: "`--top N` prints only the first `N` lines of the ordered output."
  Source: "The default is `3`."
  Source: "If there are fewer than `N` categories, all of them are printed."
  Aspect: `python tally.py report FILE --top N` uses the `--top N` option with `N` equal to `2` on the worked example -> stdout is `rent: 800.00\nbooks: 19.75\n`
  Aspect: `python tally.py report FILE` reads four ordered categories without `--top` -> stdout contains exactly the first `3` category lines
  Aspect: `python tally.py report FILE --top N` uses `N` equal to `10` on a CSV with two categories -> stdout contains both category lines
  Example: the worked example with no `--top` -> stdout `rent: 800.00\nbooks: 19.75\nfood: 19.75\n`
  Verification: `tests/test_top.py::test_limit_after_sorting`, `tests/test_top.py::test_default_three`, and `tests/test_top.py::test_fewer_categories_than_limit` assert exact output for each situation.
- F5.AC3: Accept N only when every character is an ASCII digit and its numeric value is within 1 through 10 inclusive.
  Source: "`N` must consist only of the digits `0` to `9` and have a value from `1` to `10` inclusive."
  Aspect: `python tally.py report FILE --top N` uses `N` consisting only of digits `0` to `9` with numeric value `1`, `9`, or `10` on a valid CSV -> the command exits with code `0`
  Aspect: `python tally.py report FILE --top N` uses `01` on a valid CSV -> stdout contains the first `1` ordered category line
  Aspect: `python tally.py report FILE --top N` uses any of `0`, `11`, `-1`, `+1`, `1.0`, ` 1 `, an empty argument, or non-ASCII digits -> the command exits with code `2`
  Example: `--top 1` -> selected line limit `1`
  Example: `--top 10` -> selected line limit `10`
  Example: `--top 0` -> exit code `2`
  Example: `--top 11` -> exit code `2`
  Verification: `tests/test_top.py::test_valid_top_values` covers inclusive boundaries and leading zeros; `tests/test_top.py::test_invalid_top_values` parameterises syntax and range violations.
- F5.AC4: When --top occurs repeatedly, use and validate only its last value.
  Source: "If `--top` is given more than once, only the last one is used (and only that one is checked)."
  Aspect: `python tally.py report FILE --top abc --top 2` reads the worked example with repeated `--top` occurrences -> stdout is `rent: 800.00\nbooks: 19.75\n`
  Aspect: `python tally.py report FILE --top 99 --top 1` reads the worked example -> stdout is `rent: 800.00\n`
  Aspect: `python tally.py report FILE --top 2 --top abc` reads the worked example -> stderr is `error: --top must be between 1 and 10\n`
  Example: `report sample.csv --top abc --top 2` -> exit code `0`
  Verification: `tests/test_top.py::test_only_last_top_is_checked` checks ignored invalid earlier values, final selection, and an invalid final value.
- F5.AC5: Invalid final N produces the exact error, empty stdout, and status 2 before FILE is read, including when FILE does not exist.
  Source: "Otherwise the tool prints `error: --top must be between 1 and 10` to standard error, prints nothing to standard output, and exits with code `2`."
  Source: "This is checked before FILE is read, so an invalid `--top` is reported even when FILE does not exist."
  Aspect: `python tally.py report FILE --top 0` uses a nonexistent FILE -> stderr is `error: --top must be between 1 and 10\n`
  Aspect: `python tally.py report FILE --top 0` uses a nonexistent FILE -> stdout is empty
  Aspect: `python tally.py report FILE --top 0` uses a nonexistent FILE -> the command exits with code `2`
  Aspect: `python tally.py report FILE --top abc` runs with file reads guarded against access -> FILE read attempts equal `0`
  Example: `report missing.csv --top 0` -> stderr `error: --top must be between 1 and 10\n`, exit code `2`
  Example: invalid final `--top` text `abc` -> diagnostic message `error: --top must be between 1 and 10`
  Verification: `tests/test_top.py::test_invalid_top_precedes_file_read` asserts the CLI error tuple for a missing path and uses a guarded in-process command invocation to prove no file-read attempt.

### F6 Errors / unreadable FILE and invalid amounts

Outcome: File-read failures and the first invalid amount produce precise stderr diagnostics and status 1 without any partial stdout.

Acceptance criteria:
- F6.AC1: Any inability to read FILE produces its exact command-line spelling in the error, no stdout, and exit code 1.
  Source: "If FILE cannot be read (it does not exist, or it is a directory, or anything else prevents reading it), print `error: cannot read <FILE>` to standard error, with `<FILE>` exactly as given on the command line."
  Source: "Print nothing to standard output and exit with code `1`."
  Aspect: `python tally.py report ./missing.csv` refers to a nonexistent file -> stderr is `error: cannot read ./missing.csv\n`
  Aspect: `python tally.py report FILE` refers to a directory -> stderr contains `error: cannot read <FILE>` with `<FILE>` replaced by the exact supplied argument
  Aspect: `python tally.py report FILE` encounters a guarded read raising a permission or mid-read error -> stderr contains `error: cannot read <FILE>` with `<FILE>` replaced by the exact supplied argument
  Aspect: `python tally.py report FILE` encounters an inability to read FILE -> stdout is empty
  Aspect: `python tally.py report FILE` encounters an inability to read FILE -> the command exits with code `1`
  Example: `report ./missing.csv` -> stderr `error: cannot read ./missing.csv\n`, exit code `1`
  Verification: `tests/test_errors.py::test_unreadable_file` covers missing paths and directories with real CLI calls; `tests/test_errors.py::test_read_failure` injects permission and mid-read errors into the command entry point and asserts stderr, stdout, and status, including preservation of path spelling.
- F6.AC2: Report only the first invalid amount using its original text and its physical 1-based line number, counting the header as line 1; exit 1 with no stdout even after earlier valid rows.
  Source: "If any row has an invalid amount, print `error: line <n>: invalid amount '<value>'` to standard error, where `<n>` is the 1-based line number in the file of the first such row (the header is line 1) and `<value>` is the amount text exactly as written."
  Source: "Print nothing at all to standard output, even if earlier rows were fine, and exit with code `1`."
  Aspect: `python tally.py report FILE` reads header line `1`, one valid row on line `2`, ` 4.5 ` on line `3`, and `abc` on line `4` -> stderr is `error: line 3: invalid amount ' 4.5 '\n`, instantiating `error: line <n>: invalid amount '<value>'` with `<n>` equal to `3` and `<value>` equal to ` 4.5 `
  Aspect: `python tally.py report FILE` reads valid transactions followed by an invalid amount -> stdout is empty
  Aspect: `python tally.py report FILE` reads valid transactions followed by an invalid amount -> the command exits with code `1`
  Aspect: `python tally.py report FILE --top 1` reads more than one category followed by an invalid amount beyond the prospective output limit -> stderr reports the invalid row using `error: line <n>: invalid amount '<value>'`
  Example: a header, valid row, then row with `abc` -> stderr `error: line 3: invalid amount 'abc'\n`, exit code `1`
  Verification: `tests/test_errors.py::test_first_invalid_amount` checks exact line numbering and preserved spaces when multiple rows are invalid; `tests/test_errors.py::test_no_partial_output` asserts empty stdout after valid rows; `tests/test_errors.py::test_top_does_not_hide_invalid_rows` checks that all input is validated before output selection.

### F7 Empty input

Outcome: A header-only CSV reports no data successfully.

Acceptance criteria:
- F7.AC1: A file containing only the header prints no data plus a newline and exits successfully.
  Source: "If FILE contains only the header line, print `no data` followed by a newline to standard output and exit with code `0`."
  Source: "Nothing else is written to standard output, and standard error stays empty."
  Aspect: `python tally.py report FILE` reads only `date,category,amount` -> stdout is `no data\n`
  Aspect: `python tally.py report FILE` reads only the header -> the command exits with code `0`
  Aspect: `python tally.py report FILE` reads only the header -> stderr is empty
  Example: FILE contains `date,category,amount\n` -> stdout `no data\n`, exit code `0`
  Example: FILE contains only its header -> output message `no data`
  Verification: `tests/test_empty.py::test_header_only` asserts complete stdout, stderr, and exit status, both with the default and an explicit valid --top.

### F8 Worked example

Outcome: The provided sample produces exactly the specified three lines, demonstrating grouping, sorting, ties, and the default limit together.

Acceptance criteria:
- F8.AC1: The supplied sample CSV yields the exact worked report and exit code 0.
  Source: "Given `sample.csv`:"
  Source: "date,category,amount"
  Source: "2024-01-03,food,12.50"
  Source: "2024-01-05,rent,800"
  Source: "2024-01-09,food,7.25"
  Source: "2024-01-11,books,19.75"
  Source: "2024-01-12,travel,19.75"
  Source: "Running `report sample.csv` prints (exit code `0`):"
  Source: "rent: 800.00"
  Source: "books: 19.75"
  Source: "food: 19.75"
  Source: "`food` totals `19.75`, the same as `books` and `travel`, so those three are ordered by name; `rent` is first because `800` is the largest total."
  Source: "Only three lines are printed because the default for `--top` is `3`, so `travel` is cut."
  Aspect: `python tally.py report sample.csv` reads the exact provided header and dated rows with `food` amounts `12.50` and `7.25`, `rent` amount `800`, and `books` and `travel` amounts `19.75` -> stdout is `rent: 800.00\nbooks: 19.75\nfood: 19.75\n`
  Aspect: `python tally.py report sample.csv` reads `sample.csv` containing the supplied five transactions without `--top` -> the command exits with code `0`
  Aspect: `python tally.py report sample.csv` reads the supplied five transactions without `--top` -> stdout has exactly `3` lines
  Example: `report sample.csv` with the issue's CSV -> stdout `rent: 800.00\nbooks: 19.75\nfood: 19.75\n`, exit code `0`
  Verification: `tests/test_example.py::test_worked_example` writes the exact supplied CSV, runs the documented Python entry point, and asserts complete stdout, empty stderr, and status 0.

## Decisions and assumptions

- This is a greenfield build: the clone contains only repository guidance, project metadata, and the lockfile, with no application or earlier tests to preserve. Do not invent regression criteria or test retirements. (source: rung 1: repository file inventory; `pyproject.toml:1`)
- Implement the application as the root-level `tally.py`; support the `report` subcommand and the optional two-word --top syntax. (source: rung 2: "python tally.py report FILE [--top N]")
- Keep runtime dependencies empty and use Python 3.12's standard library. Test tooling such as pytest is separate from the application's runtime dependency constraint. (source: rung 1: `pyproject.toml:4`, `pyproject.toml:5`, `AGENTS.md:20`; rung 2: "Build a command-line tool, written in Python 3.12 using only the standard library, that summarises a CSV file of transactions by category.")
- Put the named automated tests in root-level `tests/`, with real behavioural assertions; all added production lines must execute during tests, and ruff, strict mypy, and the repository's source/structure rules must pass without suppression or weakened configuration. (source: rung 1: `AGENTS.md:14`, `AGENTS.md:20`, `AGENTS.md:23`, `AGENTS.md:32`, `AGENTS.md:51`, `AGENTS.md:72`)
- Use a 10-second timeout on every subprocess-based CLI test, preserve the inherited environment, and do not use Python isolation flags that prevent child coverage. The timeout value is an assumption reversible by updating test helpers if the build environment requires another bounded timeout. (source: rung 1: `AGENTS.md:25`, `AGENTS.md:30`; rung 4: default)
- Use `decimal.Decimal` for exact addition and formatting; choose a decimal precision sufficient for every valid input amount and its cumulative sum rather than relying on the default precision. No amount-size limit is introduced. This implementation assumption is reversible by replacing the arithmetic representation with another demonstrably exact decimal implementation and retaining the observable tests. (source: rung 4: default; rung 2: "Add them as decimals, never as binary floating point: `0.10` plus `0.20` is exactly `0.30`.")
- Interpret amount digits as ASCII `0` through `9`, accept zero and leading zeros, and do not trim amount text. ASCII interpretation is an assumption reversible by broadening the numeric grammar if later requirements explicitly admit other digit sets. (source: rung 4: default; rung 2: "`amount` is valid when it is one or more digits, optionally followed by a point and then one or two digits.")
- Preserve category text exactly and compare category strings without locale collation, normalization, or whitespace trimming. (source: rung 2: "A category name is used exactly as written: it is case-sensitive, and spaces are part of the name, so `food` and ` food` (leading space) are two different categories, and a space is printed as written."; "Categories with equal totals are ordered by category name in ascending code-point order (plain string comparison, so `a` comes before `b`, and `B` before `a`).")
- Validate only the last --top value before opening FILE, then read and validate the entire input before printing any totals; --top limits output, not input validation. (source: rung 2: "If `--top` is given more than once, only the last one is used (and only that one is checked)."; "This is checked before FILE is read, so an invalid `--top` is reported even when FILE does not exist."; "Print nothing at all to standard output, even if earlier rows were fine, and exit with code `1`.")
- Append exactly one newline to each stderr diagnostic. The issue specifies diagnostic text but not its terminator; this assumption is reversible by adjusting error rendering and error assertions if a subsequent requirement specifies another terminator. (source: rung 4: default)
- Treat UTF-8 decoding failures and errors during file reading as cannot-read errors with the issue's FILE diagnostic and status 1. Decoding-failure classification is an assumption reversible by defining a separate encoding diagnostic in future requirements. (source: rung 4: default; rung 2: "If FILE cannot be read (it does not exist, or it is a directory, or anything else prevents reading it), print `error: cannot read <FILE>` to standard error, with `<FILE>` exactly as given on the command line.")
- Use standard text-file newline handling for LF and CRLF input; remove only record terminators, never field spaces. Newline handling is an assumption reversible by changing the reader if a later specification restricts line endings. (source: rung 3: Python standard-library text I/O newline behaviour)
- The guaranteed header, field count, unquoted fields, nonempty comma-free categories, and absence of blank lines are input preconditions. Do not add required malformed-CSV features or date validation. (source: rung 2: "Fields are never quoted, a category is never empty and never contains a comma, and the file contains no blank lines."; "Dates are not checked at this stage.")
- For command syntax outside the documented grammar, choose status 2 with a usage diagnostic on stderr and no stdout, including joined --top spelling, missing arguments, unknown options, and unknown commands. Exact usage wording is left to the standard-library parser. This assumption is reversible by documenting another CLI grammar and updating the usage-error tests. (source: rung 4: default; rung 2: "`--top` is optional and is written as two separate words, `--top` then `N`.")
- Build and deliver only in the supplied fixture; never branch, push, or open a pull request. This is a workflow constraint, not an application feature requiring a runtime test. (source: rung 1: the task's fixture-workspace instructions)
