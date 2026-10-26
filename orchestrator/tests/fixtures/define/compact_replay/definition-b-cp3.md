# Category expressions, required categories, coded errors, and summary reports

## Objective

Extend the existing Python `tally.py report` command with category-expression filtering, assertions about required categories, structured error reporting, and an aggregate JSON summary. Preserve all accepted behavior from parts 1 and 2 when the new options are absent.

## Features

### F1 Report command and option handling

Outcome: The report command accepts the extended usage while retaining the established behavior of `--top`, `TALLY_TOP`, `--format`, and `--since`.

Acceptance criteria:
- F1.AC1: All earlier report behavior remains unchanged when no new option is supplied.
  Source: "Extend the `report` command from parts 1 and 2."
  Source: "Everything from parts 1 and 2 keeps working exactly as before: when none of the new options below is used, the output, exit codes and error messages are unchanged."
  Kind: regression
  Verification: All existing tests under `tests/` keep passing, including `tests/test_cli.py`, `tests/test_options.py`, `tests/test_environment.py`, `tests/test_format.py`, `tests/test_since.py`, `tests/test_amounts.py`, `tests/test_ordering.py`, `tests/test_errors.py`, and `tests/test_runtime_extensions.py`.
- F1.AC2: The extended invocation accepts separate-word value options in any order, repeated requirements, and the valueless summary flag.
  Source: "The full usage is now:"
  Source: "python tally.py report FILE [--top N] [--format FORMAT] [--since DATE] [--where EXPR] [--require NAME]... [--errors MODE] [--summary]"
  Source: "The options from part 2 and `--where` and `--errors` are written as the option name followed by a separate value word, and may appear in any order."
  Source: "`--require` is the exception: it may be given any number of times and every value is used, in command-line order."
  Source: "`--summary` is a flag: it has no value word."
  Aspect: `python tally.py report FILE` supplies separate values for `--top`, `--format`, `--since`, `--where`, `--require`, and `--errors`, plus `--summary`, in permutations of option order with valid values and matching data -> the command exits with code `0`
  Aspect: `report FILE --require food --require books` reads data containing both categories -> the command exits with code `0`
  Aspect: `report FILE --summary --top 1` reads valid data -> stdout is the summary JSON object
  Example: `report FILE --summary` reads a header-only file -> stdout `{"rows": 0, "categories": 0, "total": "0.00", "top": []}\n`
  Verification: Extend `tests/test_options.py` with CLI permutation tests, repeated requirements, and summary placement tests using the existing `run` fixture.
- F1.AC3: For each single-value option, only its final supplied value is validated and used.
  Source: "If one of them is given more than once, only its last value is used (and only that one is checked)."
  Aspect: `report FILE` repeats each of `--top`, `--format`, `--since`, `--where`, and `--errors` with an invalid earlier value and a valid last value -> its result equals the invocation with only the last value
  Aspect: `report FILE` repeats each single-value option with a valid earlier value and an invalid last value -> stderr contains the diagnostic for the last value
  Verification: Extend `tests/test_options.py` with parameterized tests of invalid-then-valid and valid-then-invalid repetitions, checking exit status and both streams.

### F2 Category expressions with --where

Outcome: A valid expression filters rows by their exact category before aggregation, in conjunction with the existing date filter.

Acceptance criteria:
- F2.AC1: Expression matching and date filtering jointly determine the kept rows, with case-sensitive category identity.
  Source: "`--where EXPR` keeps only the rows whose category satisfies the expression `EXPR`."
  Source: "It combines with `--since`: a row is kept when it passes both."
  Source: "A row's category is compared exactly as written, so names are case-sensitive."
  Aspect: `report FILE` uses `--where EXPR` with `EXPR` equal to `food` and reads rows in categories `food` and `Food` -> stdout reports only the total for `food`
  Aspect: `report FILE --since 2024-03-03 --where food` reads matching and nonmatching categories before, on, and after the date boundary -> stdout reports the total of only `food` rows on or after `2024-03-03`
  Verification: Create `tests/test_where.py` with CLI tests that assert exact output in text and JSON formats, including the inclusive date boundary and case distinctions.
- F2.AC2: Parse the complete grammar, including recursive negation, parentheses, ASCII names, and the stated operator precedence.
  Source: "An expression follows this grammar:"
  Source: "expr   := term ( "|" term )*"
  Source: "term   := factor ( "&" factor )*"
  Source: "factor := "!" factor | "(" expr ")" | NAME"
  Source: "`NAME` is one or more of the characters `A-Z`, `a-z`, `0-9` and `_`."
  Source: "A row satisfies `NAME` when its category is exactly `NAME`."
  Source: "(A category with any other character cannot be named; it is matched only through `!`.)"
  Source: "`|` means or, `&` means and, `!` means not, and parentheses group."
  Source: "`!` binds tighter than `&`, and `&` binds tighter than `|`."
  Aspect: `report FILE --where A_z09` reads categories named with `A-Z`, `a-z`, `0-9`, and `_`, including exact `NAME` value `A_z09` -> stdout reports only `A_z09`
  Aspect: `report FILE --where food|rent&books` uses `|` for or and `&` for and and reads `food`, `rent`, and `books` rows -> stdout reports only `food`
  Aspect: `report FILE --where !food&rent|books` reads `food`, `rent`, `books`, and `travel` rows -> stdout reports only `rent` and `books` categories
  Aspect: `report FILE --where !!food` reads `food` and `rent` rows -> stdout reports only `food`
  Aspect: `report FILE --where !food` reads `food`, `café`, `a-b`, and a category containing spaces -> stdout reports the categories other than `food`
  Aspect: `report FILE --where !(food|rent)` reads `food`, `rent`, and `books` rows -> stdout reports only `books`
  Verification: `tests/test_where.py` supplies CLI truth-table cases for each operator, precedence, nested parentheses, recursive negation, exact ASCII names, and categories that cannot be named.
- F2.AC3: The three illustrative expressions select the stated categories.
  Source: "Examples: `food|rent` keeps the rows of `food` and of `rent`; `(food|rent)&books` keeps nothing, because no category is two names at once; `!(food|rent)` keeps every row that is neither."
  Aspect: `report FILE --where food|rent` reads `food`, `rent`, and `books` with one unit each -> stdout is `food: 1.00\nrent: 1.00\n`
  Aspect: `report FILE --where (food|rent)&books` reads those three categories -> stdout is `no data\n`
  Aspect: `report FILE --where !(food|rent)` reads those three categories with one unit each -> stdout is `books: 1.00\n`
  Example: `report FILE --where (food|rent)&books` on those categories -> stdout `no data\n`
  Verification: `tests/test_where.py` asserts exact stdout for all three issue examples.
- F2.AC4: Empty expressions and all grammar violations, including whitespace, produce the specified invalid-expression error.
  Source: "No spaces are allowed anywhere in an expression."
  Source: "An empty expression, or any text that does not follow the grammar, prints `error: invalid expression '<value>'` to standard error, with `<value>` exactly as given, prints nothing to standard output, and exits with code `2`."
  Aspect: `report FILE --where` receives an empty value or an invalid expression, including spaces, tabs, newlines, illegal name characters, missing operands, unmatched parentheses, or trailing tokens -> stderr is `error: invalid expression '<value>'\n` with the original value substituted literally
  Aspect: `report FILE --where` receives any of those invalid values -> stdout is empty
  Aspect: `report FILE --where` receives any of those invalid values -> the command exits with code `2`
  Example: `report FILE --where food rent` with `food rent` supplied as one argument -> stderr `error: invalid expression 'food rent'\n`, exit code `2`
  Verification: `tests/test_where.py` parameterizes invalid expressions and asserts the exact diagnostic, preserved input, exit code, and empty stdout.

### F3 Required categories with --require

Outcome: Required categories must occur in the filtered row set, without restricting or otherwise changing the report.

Acceptance criteria:
- F3.AC1: Each required category is checked by exact identity against kept rows, independently of the report's top limit.
  Source: "`--require NAME` asserts that the category `NAME` has at least one row among the rows kept by `--since` and `--where`."
  Source: "It does not change which rows are reported."
  Source: "The value is compared exactly as written, like a category name in part 1."
  Aspect: `report FILE --top 1` uses `--require NAME` with `NAME` equal to `books` and reads kept `books` rows below the highest total -> the command exits with code `0`
  Aspect: `report FILE --require books` reads kept `books` rows plus other categories -> stdout equals the same report without `--require`
  Aspect: `report FILE --require Food` reads only kept `food` rows -> stderr is `error: category 'Food' not found\n`
  Aspect: `report FILE --require café` reads a kept category exactly named `café` -> the command exits with code `0`
  Aspect: `report FILE --require books` reads `books` rows that are all excluded by `--since` or `--where` -> stderr is `error: category 'books' not found\n`
  Verification: Create `tests/test_require.py` with CLI tests of identity, Unicode and whitespace names, filters, and requirements satisfied by categories outside the displayed top array.
- F3.AC2: Only an empty requirement value is rejected as an invalid category name, with the specified option error.
  Source: "A `--require` value must be a non-empty category name."
  Source: "Otherwise print `error: --require must not be empty` to standard error, nothing to standard output, and exit with code `2`."
  Aspect: `report FILE --require` is passed the empty string as its value -> stderr is `error: --require must not be empty\n`
  Aspect: `report FILE --require` is passed the empty string as its value -> stdout is empty
  Aspect: `report FILE --require` is passed the empty string as its value -> the command exits with code `2`
  Example: `report FILE --require ""` -> stderr `error: --require must not be empty\n`, exit code `2`
  Verification: `tests/test_require.py` checks empty values at multiple positions in the requirement list, including an empty value following a missing non-empty category.
- F3.AC3: Absent required categories cause a data error, reporting only the first absent category in argument order.
  Source: "If no kept row has category `NAME`, print `error: category '<NAME>' not found` to standard error, nothing to standard output, and exit with code `1`."
  Source: "When several required categories are absent, only the first one in command-line order is reported."
  Aspect: `report FILE --require NAME` has no kept row named `NAME` -> stderr is `error: category 'NAME' not found\n`
  Aspect: `report FILE --require NAME` has no kept row named `NAME` -> stdout is empty
  Aspect: `report FILE --require NAME` has no kept row named `NAME` -> the command exits with code `1`
  Aspect: `report FILE --require food --require books --require travel` has kept `food` rows but no kept `books` or `travel` rows -> stderr is `error: category 'books' not found\n`
  Example: `report FILE --require books --require travel` with neither category kept -> stderr `error: category 'books' not found\n`, exit code `1`
  Verification: `tests/test_require.py` asserts complete failure results for single and multiple absent requirements and duplicate requirements.

### F4 Text and JSON errors, codes, and locations

Outcome: All reported errors have semantic codes; selecting JSON errors exposes their code, original diagnostic message, and list-element location when applicable.

Acceptance criteria:
- F4.AC1: Error mode defaults to text; explicit text preserves earlier and newly specified plain diagnostics.
  Source: "`--errors` is `text` or `json`."
  Source: "The default is `text`: errors are printed exactly as in the earlier parts and above."
  Aspect: `report FILE` produces a supported error with `--errors` omitted -> stderr equals its established plain diagnostic
  Aspect: `report FILE --errors text` produces the same error -> stderr equals its established plain diagnostic
  Aspect: `report FILE --errors json` selects mode `json` and has an invalid expression -> stderr is a JSON error object
  Example: `report FILE --where "" --errors text` -> stderr `error: invalid expression ''\n`
  Verification: Create `tests/test_error_modes.py` with parameterized text-mode tests covering old and new errors and equality to the omitted-mode result.
- F4.AC2: JSON error output preserves exit codes and messages while using exactly the specified keys, order, separators, Unicode encoding, and newline.
  Source: "With `--errors json`, an error is printed to standard error as one JSON object followed by a newline, instead of the `error: ...` text."
  Source: "Standard output stays empty and the exit code is the same as for the text form."
  Source: "The object has the keys `code` and `message`, in that order, and then `location` when the error has one."
  Source: "Use the default separators of Python's `json.dumps` and write non-ASCII characters as they are (`ensure_ascii=False`)."
  Source: "`message` is the text the plain form would print after the leading `error: `, so the plain form `error: line 4: invalid amount 'x'` gives `"message": "line 4: invalid amount 'x'"`."
  Aspect: `report FILE --errors json` produces a supported error whose plain diagnostic uses the `error: ...` form with leading `error:` -> stderr equals one JSON object serialized with default `json.dumps` separators and `ensure_ascii=False`, followed by `\n`
  Aspect: `report FILE --errors json` produces each supported error -> stdout is empty
  Aspect: `report FILE --errors json` produces each supported error -> the exit code equals the corresponding text-mode exit code
  Aspect: `report FILE --errors json` produces an error without a list location -> the object's ordered keys are `code`, `message`
  Aspect: `report FILE --errors json` produces an error with a list location -> the object's ordered keys are `code`, `message`, `location`
  Aspect: `report FILE --errors json` encounters invalid amount `x` on file line `4`, whose plain diagnostic is `error: line 4: invalid amount 'x'` -> the `message` field is `line 4: invalid amount 'x'`
  Aspect: `report FILE --errors json` encounters invalid amount `x` on file line `4` -> stderr contains the field fragment `message": "line 4: invalid amount 'x'`
  Example: `report FILE --require café --errors json` with the category absent -> stderr `{"code": "MISSING_CATEGORY", "message": "category 'café' not found", "location": "require[0]"}\n`
  Verification: `tests/test_error_modes.py` compares exact stderr bytes, parsed field order, messages, exits, empty stdout, escaping, and non-ASCII output for every error family.
- F4.AC3: Invalid error modes always use the specified plain diagnostic, regardless of earlier mode values.
  Source: "Any other value of `--errors` prints `error: --errors must be text or json` to standard error, always in this plain form, nothing to standard output, and exits with code `2`."
  Aspect: `report FILE --errors MODE` has a final mode other than `text` or `json` -> stderr is `error: --errors must be text or json\n`
  Aspect: `report FILE --errors MODE` has an invalid final mode -> stdout is empty
  Aspect: `report FILE --errors MODE` has an invalid final mode -> the command exits with code `2`
  Example: `report FILE --errors json --errors xml` -> stderr `error: --errors must be text or json\n`, exit code `2`
  Verification: `tests/test_error_modes.py` parameterizes empty, case-variant, whitespace, and unknown final mode values and confirms that a prior JSON mode has no effect.
- F4.AC4: Exactly one semantic code is selected for every error family from all three parts.
  Source: "Every error the tool reports (from this part and from parts 1 and 2) is reported with exactly one of these codes, chosen by what the error means:"
  Source: "`INVALID_OPTION`: the request itself is not acceptable as written."
  Source: "`UNREADABLE_FILE`: the input file could not be opened or read."
  Source: "`INVALID_AMOUNT`: a row of the input has an amount that is not valid."
  Source: "`MISSING_CATEGORY`: a category the request depends on is absent from the data."
  Aspect: `report FILE --errors json` has invalid request syntax or an invalid top, format, date, expression, or empty requirement -> the error object's `code` is `INVALID_OPTION`
  Aspect: `report FILE --errors json` encounters an open, read, or UTF-8 decoding failure -> the error object's `code` is `UNREADABLE_FILE`
  Aspect: `report FILE --errors json` encounters an invalid input amount -> the error object's `code` is `INVALID_AMOUNT`
  Aspect: `report FILE --errors json` has an absent required category -> the error object's `code` is `MISSING_CATEGORY`
  Example: `report FILE --top 0 --errors json` -> stderr `{"code": "INVALID_OPTION", "message": "--top must be between 1 and 10"}\n`
  Verification: `tests/test_error_modes.py` covers structural request errors, each value validator including `TALLY_TOP`, unreadable files and decoding/read failures, invalid amounts, and absent categories; invalid error-mode requests are checked by F4.AC3.
- F4.AC5: Locations use the original zero-based list positions; non-list errors omit location entirely.
  Source: "An error caused by one element of a list carries a `location` that names the element by its zero-based position in its list, written `<list>[<index>]`."
  Source: "The lists are `rows` (the data rows of the file in file order, not counting the header) and `require` (the `--require` values in command-line order)."
  Source: "For example, an invalid amount on file line 4 is the third data row, so its location is `rows[2]`:"
  Source: "{"code": "INVALID_AMOUNT", "message": "line 4: invalid amount 'x'", "location": "rows[2]"}"
  Source: "An error that is not caused by one element of such a list has no `location` key."
  Aspect: `report FILE --errors json` reads invalid amount `x` on file line `4`, after two data rows -> the error location is `rows[2]`
  Aspect: `report FILE --since 2024-03-03 --where food --errors json` reads invalid amount `x` on file line `4` even though preceding rows or that row are filtered out -> the error location is `rows[2]`
  Aspect: `report FILE --require food --require "" --errors json` receives an empty second requirement -> the error location is `require[1]`
  Aspect: `report FILE --require food --require books --errors json` has kept `food` but no kept `books` -> the error location is `require[1]`
  Aspect: `report FILE --errors json` fails for an unreadable file or an invalid non-list option -> the error object has no `location` key
  Example: Invalid amount `x` on file line `4` in JSON error mode -> stderr `{"code": "INVALID_AMOUNT", "message": "line 4: invalid amount 'x'", "location": "rows[2]"}\n`
  Verification: `tests/test_error_modes.py` asserts complete objects for original row positions, filtered invalid rows, empty and missing requirements, duplicate requirements, and non-list failures.

### F5 Summary output with --summary

Outcome: Summary mode returns counts and the exact overall total for every kept row, alongside the established top-category JSON array.

Acceptance criteria:
- F5.AC1: Summary mode replaces ordinary report output with one ordered JSON object and newline.
  Source: "`--summary` replaces the normal output with a summary of the rows kept by `--since` and `--where`."
  Source: "Standard output is one JSON object followed by a newline, with exactly the keys `rows`, `categories`, `total` and `top`, in that order:"
  Source: "Use the default separators of `json.dumps` and `ensure_ascii=False`."
  Aspect: `report FILE --summary --since 2024-03-03 --where !rent` reads rows passing and failing both filters -> stdout is one summary JSON object describing only kept rows, followed by `\n`
  Aspect: `report FILE --summary` reads valid data -> the object's ordered keys are exactly `rows`, `categories`, `total`, `top`
  Aspect: `report FILE --summary` reads Unicode categories -> stdout equals serialization with default `json.dumps` separators and `ensure_ascii=False`
  Verification: Create `tests/test_summary.py` with exact-output CLI assertions, key-order assertions, joint filtering, and Unicode/escaping cases.
- F5.AC2: Counts and overall total describe all kept rows, even when the top array is truncated.
  Source: "`rows`: the number of kept rows, as a JSON integer."
  Source: "`categories`: the number of distinct categories among the kept rows, as a JSON integer."
  Source: "`total`: the sum of the amounts of all kept rows, as a string with exactly two decimals."
  Aspect: `report FILE --summary --top 1` reads four kept rows in three distinct categories -> `rows` is the JSON integer `4`
  Aspect: `report FILE --summary --top 1` reads four kept rows in three distinct categories -> `categories` is the JSON integer `3`
  Aspect: `report FILE --summary --top 1` reads kept amounts `12.50`, `7.25`, `4.5`, and `19.75` -> `total` is the string `44.00`
  Example: Summary for those four kept amounts -> `total` is `"44.00"`
  Verification: `tests/test_summary.py` asserts integer types and counts, totals independent of top truncation, exact decimal addition, and totals beyond the default Decimal precision/exponent limits.
- F5.AC3: The summary top array equals the existing JSON report's category array for the same kept rows and top limit.
  Source: "`top`: the same array that `--format json` prints (one object per category, in the same order, cut by `--top`)."
  Aspect: `report FILE --summary --top 2` reads kept categories with tied totals -> `top` equals the parsed output of `report FILE --format json --top 2` with the same filters
  Aspect: `report FILE --summary` uses a valid `TALLY_TOP` with no explicit top -> `top` equals the existing JSON report array under the same environment
  Example: Kept `food` and `travel` totals both `19.75`, with `--top 2` and lower `books` total -> `top` is `[{"category": "food", "total": "19.75"}, {"category": "travel", "total": "19.75"}]`
  Verification: `tests/test_summary.py` asserts equality to ordinary JSON output, exact category-object shape, inherited tie ordering, explicit limits, defaults, and environment limits.
- F5.AC4: Empty summaries use the specified object and succeed.
  Source: "If no rows are kept, the object is `{"rows": 0, "categories": 0, "total": "0.00", "top": []}` (not `no data`)."
  Source: "The exit code is `0`."
  Aspect: `report FILE --summary` reads a header-only file or filters out every row -> stdout is `{"rows": 0, "categories": 0, "total": "0.00", "top": []}\n` instead of `no data`
  Aspect: `report FILE --summary` completes successfully with empty or nonempty kept rows -> the command exits with code `0`
  Example: `report FILE --summary` with no kept rows -> stdout `{"rows": 0, "categories": 0, "total": "0.00", "top": []}\n`, exit code `0`
  Verification: `tests/test_summary.py` asserts exact stdout and status for header-only files, empty expression results, all-date-filtered data, and nonempty summaries.
- F5.AC5: Format values remain validated in summary mode but valid values do not affect the summary.
  Source: "In this mode the `--format` value is still validated but has no effect on the output."
  Aspect: `report FILE --summary --format xml` reads otherwise valid data -> stderr is `error: --format must be text or json\n`
  Aspect: `report FILE --summary` is run with omitted format, `--format text`, and `--format json` on identical data -> stdout is identical across all three invocations
  Verification: `tests/test_summary.py` compares all valid format choices and asserts complete error results for invalid formats in text and JSON error modes.

### F6 Order of checks

Outcome: Exactly the first error in the stated validation sequence is emitted, with option failures resolved before opening the input file.

Acceptance criteria:
- F6.AC1: For structurally valid requests, value and data validation follow the complete stated order, independent of option placement.
  Source: "Only the first error is reported, in this order: `--errors`, the top value (`--top`, or `TALLY_TOP` when `--top` is absent), `--format`, `--since`, `--where`, the `--require` values (each must be non-empty, in command-line order), FILE cannot be read, invalid amounts, and finally the `--require` categories that are absent (in command-line order)."
  Aspect: `report FILE` has several failing checks among `--errors`, `--top` or `TALLY_TOP`, `--format`, `--since`, `--where`, `--require` values, file reading, amounts, and absent categories -> stderr contains exactly the diagnostic of the earliest failing check in the stated sequence
  Aspect: `report FILE` contains multiple empty `--require` values in JSON error mode -> the error location names the first empty value in command-line order
  Aspect: `report FILE` has valid non-empty requirements but multiple absent categories after successful data validation -> stderr names the first absent requirement in command-line order
  Aspect: `report FILE --where food --require books --errors json` reads an invalid amount in an excluded `rent` row while `books` is absent -> the error code is `INVALID_AMOUNT`
  Verification: Extend `tests/test_validation_order.py` with a parameterized precedence matrix, option permutations, environment-top fallback, requirement indices, and invalid amounts in rows excluded by either filter.
- F6.AC2: All checks before file access complete without reading the input.
  Source: "Everything before FILE is read is reported without reading it."
  Aspect: `report FILE` has an invalid mode, top, format, since, expression, or empty requirement while file opening is guarded -> the number of file-open attempts is `0`
  Verification: Extend `tests/test_validation_order.py` using its existing guarded-open and direct-main test pattern for every new pre-read validator, checking the complete error result and zero open attempts.
- F6.AC3: Invalid mode errors are always plain, while all later errors follow the selected mode.
  Source: "An `--errors` error is always plain; every later error uses the form chosen by `--errors`."
  Aspect: `report FILE --errors xml` also has an invalid top and missing file -> stderr is `error: --errors must be text or json\n`
  Aspect: `report FILE --errors json` fails each later check in the sequence -> stderr is the corresponding single JSON error object followed by `\n`
  Aspect: `report FILE --errors text` fails each later check in the sequence -> stderr is the corresponding plain diagnostic
  Verification: `tests/test_validation_order.py` exercises the complete precedence matrix in both error modes and checks invalid mode's plain output even when later checks would also fail.

### F7 Worked examples

Outcome: The supplied sample produces the issue's exact summary and missing-category diagnostic.

Acceptance criteria:
- F7.AC1: The worked summary invocation has the exact stated output and succeeds.
  Source: "Given `sample.csv`:"
  Source: "date,category,amount"
  Source: "2024-03-01,food,12.50"
  Source: "2024-03-02,food,7.25"
  Source: "2024-03-02,books,4.5"
  Source: "2024-03-05,travel,19.75"
  Source: "2024-03-06,rent,800"
  Source: "Running `report sample.csv --where "!rent" --require books --summary --top 2` prints (exit code `0`):"
  Source: "{"rows": 4, "categories": 3, "total": "44.00", "top": [{"category": "food", "total": "19.75"}, {"category": "travel", "total": "19.75"}]}"
  Aspect: `report sample.csv --where "!rent" --require books --summary --top 2` reads the five stated sample rows -> stdout is `{"rows": 4, "categories": 3, "total": "44.00", "top": [{"category": "food", "total": "19.75"}, {"category": "travel", "total": "19.75"}]}\n`
  Aspect: That same worked command reads the stated `sample.csv` -> the command exits with code `0`
  Example: `report sample.csv --where "!rent" --require books --summary --top 2` with the issue's sample -> stdout `{"rows": 4, "categories": 3, "total": "44.00", "top": [{"category": "food", "total": "19.75"}, {"category": "travel", "total": "19.75"}]}\n`, exit code `0`
  Verification: Extend `tests/test_example.py` with the exact part-three sample fixture and a CLI assertion of status, stdout, and empty stderr; do not reuse the existing part-one sample, which has different rows.
- F7.AC2: The worked filtered requirement invocation has the exact stated JSON error and failure status.
  Source: "Running `report sample.csv --since 2024-03-03 --require books --errors json` prints nothing to standard output and, to standard error (exit code `1`):"
  Source: "{"code": "MISSING_CATEGORY", "message": "category 'books' not found", "location": "require[0]"}"
  Aspect: `report sample.csv --since 2024-03-03 --require books --errors json` reads the five stated sample rows -> stderr is `{"code": "MISSING_CATEGORY", "message": "category 'books' not found", "location": "require[0]"}\n`
  Aspect: That same worked command reads the stated `sample.csv` -> stdout is empty
  Aspect: That same worked command reads the stated `sample.csv` -> the command exits with code `1`
  Example: `report sample.csv --since 2024-03-03 --require books --errors json` with the issue's sample -> stderr `{"code": "MISSING_CATEGORY", "message": "category 'books' not found", "location": "require[0]"}\n`, exit code `1`
  Verification: `tests/test_example.py` runs this command on the part-three sample and asserts exact stderr, empty stdout, and exit status.

## Decisions and assumptions

- Preserve the earlier CSV input domain and exact category identity, including Unicode, whitespace, unchecked row dates, ignored header contents, and CRLF handling; the expression NAME restriction does not restrict CSV categories or required-category values. (source: rung 1: `tests/test_input.py:6`, `tests/test_input.py:12`, `tests/test_input.py:18`, `tests/test_output.py:25`, `tally.py:55`.)
- Preserve top validation as ASCII decimal digits representing 1 through 10, allowing leading zeros; explicit top overrides the environment, an absent or empty environment uses 3, and the last explicit top wins. (source: rung 1: `tests/test_top.py:62`, `tests/test_top.py:71`, `tests/test_top.py:87`, `tests/test_environment.py:67`, `tests/test_environment.py:164`, `tally.py:25`.)
- Preserve text/JSON report formats, inclusive lexical date filtering with strict calendar-date option validation, descending numeric totals, ascending Python string order for ties, exact Decimal arithmetic, two-place total strings, and category-object key order. Reuse these behaviors for summary top output and exact overall totals. (source: rung 1: `tests/test_format.py:33`, `tests/test_format.py:82`, `tests/test_since.py:17`, `tests/test_ordering.py:27`, `tests/test_amounts.py:73`, `tests/test_amounts.py:79`, `tests/test_amounts.py:92`, `tally.py:75`.)
- Validate amounts throughout the original file, including rows excluded by either filter and categories omitted by top; locations count original data rows. Continue reporting the first invalid amount in file order. (source: rung 1: `tests/test_since.py:97`, `tests/test_errors.py:26`, `tests/test_errors.py:100`; rung 2: "Only the first error is reported, in this order: `--errors`, the top value (`--top`, or `TALLY_TOP` when `--top` is absent), `--format`, `--since`, `--where`, the `--require` values (each must be non-empty, in command-line order), FILE cannot be read, invalid amounts, and finally the `--require` categories that are absent (in command-line order).")
- Preserve structural argument rejection before semantic value checks, and preserve the old plain usage line `usage: tally.py report FILE [--top N]` with exit 2 for malformed invocations. The expanded usage describes accepted syntax; it does not explicitly replace the protected error text. (source: rung 1: `tests/test_errors.py:45`, `tests/test_options.py:117`, `tests/test_top.py:19`, `tally.py:14`.)
- Assumption: For malformed invocations containing a recoverable complete `--errors json` option, emit the usage diagnostic as INVALID_OPTION JSON with message `usage: tally.py report FILE [--top N]` and no location, preserving exit 2. Resolve the last complete error-mode value before rendering such syntax errors; an invalid final mode emits its own specified plain error. Missing value words are structural errors, not empty values. Reverse by specifying different syntax-error rendering or mode-resolution rules and updating the corresponding new tests, while retaining no-new-option regressions. (source: rung 5: `default`; the issue's JSON-error rule supplies the rendering objective but does not specify mode recovery from malformed requests.)
- A required-category value is valid whenever it is non-empty; do not strip whitespace, normalize case, or impose expression-name syntax. Preserve duplicate values and their command-line positions, including for empty-value and absence locations. (source: rung 1: `tests/test_output.py:25`, `tally.py:55`; rung 2: "A `--require` value must be a non-empty category name." and "The value is compared exactly as written, like a category name in part 1.")
- Assumption: Repeating the valueless `--summary` flag is idempotent. Reverse by adding an explicit single-use restriction and adjusting new option tests. (source: rung 5: `default`.)
- Reject all whitespace and consume the entire expression; do not interpret expressions using Python evaluation. Use a standard-library-only grammar parser and evaluate against exact category strings. Parser/helper names and internal representations remain implementation choices. (source: rung 1: `tests/test_runtime_extensions.py:7`; rung 2: "No spaces are allowed anywhere in an expression." and "An expression follows this grammar:"; rung 4: `default` for avoiding evaluation of user input. Assumption: no extra expression-length or nesting threshold is imposed; reverse only with an explicit accepted limit and corresponding tests.)
- Keep the Python 3.12-compatible, standard-library-only, deterministic runtime with no network or clock access; reuse existing report aggregation rather than introducing a competing ordering or numeric path. (source: rung 1: `tests/test_cli.py:7`, `tests/test_runtime.py:13`, `tests/test_runtime_extensions.py:7`, `pyproject.toml:4`, `tally.py:70`.)
- Put automated tests in root `tests/`, use existing pytest fixtures and guarded-open patterns, and give every subprocess invocation a timeout while inheriting the environment. Builder validation must include existing and new tests, changed-line coverage, ruff, and strict mypy without suppressions or weakened configuration. No earlier test requires retirement. (source: rung 1: `tests/conftest.py:19`, `tests/test_validation_order.py:79`, `AGENTS.md:11`, `AGENTS.md:19`, `AGENTS.md:36`.)
- Intake changes only this feature-definition file. Builder work stays in the fixture checkout and needs no branch, remote, push, or pull request. (source: rung 2: "This is a fixture workspace for a benchmark task: there is no branch, no remote and no pull request, so do not branch or push anything.")
