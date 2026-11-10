# Report category expressions, required categories, coded errors and summary

## Objective

Extend `python tally.py report` with category expressions, assertions about required categories, structured errors and a JSON summary. Preserve all accepted parts 1 and 2 behavior when the new options are absent. Keep exact arithmetic, category identity, deterministic ordering and the repository's Python 3.12 standard-library runtime contract.

## Features

### F1 report command and option handling

Outcome: The existing report command accepts the complete part 3 invocation while preserving earlier commands and resolving option values before validation.

Acceptance criteria:

- F1.AC1: All accepted parts 1 and 2 behavior remains unchanged when no new option is used.
  Source: "Extend the `report` command from parts 1 and 2."
  Source: "Everything from parts 1 and 2 keeps working exactly as before: when none of the new options below is used, the output, exit codes and error messages are unchanged."
  Kind: regression
  Verification: All existing tests in `tests/test_tally.py` and `tests/test_tally_part2.py` remain passing, including their command-entry-point, exact arithmetic, category identity, output bytes, error, import, no-network and no-clock assertions.
- F1.AC2: Accept the complete usage with separate option/value words and arbitrary option order.
  Source: "The full usage is now:"
  Source: "python tally.py report FILE [--top N] [--format FORMAT] [--since DATE] [--where EXPR] [--require NAME]... [--errors MODE] [--summary]"
  Source: "The options from part 2 and `--where` and `--errors` are written as the option name followed by a separate value word, and may appear in any order."
  Aspect: `python tally.py report FILE --top 2 --format json --since 2024-03-01 --where food --require food --errors json --summary` reads a file with one kept `food` row of amount `1`, with options permuted as complete option/value pairs and flags -> the command exits with code `0`
  Aspect: The same `report` invocation with the options in different orders reads the same one-row file -> stdout is `{"rows": 1, "categories": 1, "total": "1.00", "top": [{"category": "food", "total": "1.00"}]}\n`
  Example: The complete invocation above on one `2024-03-01,food,1` data row -> exit code `0`
  Verification: `tests/test_tally_part3.py::test_full_usage_and_option_order` runs representative permutations of all option pairs and the flag, asserting exit status and exact stdout.
- F1.AC3: For each single-valued option, only its final value is used and validated.
  Source: "The options from part 2 and `--where` and `--errors` are written as the option name followed by a separate value word, and may appear in any order."
  Source: "If one of them is given more than once, only its last value is used (and only that one is checked)."
  Aspect: `report` reads a valid file with an invalid earlier value and valid final value for each of `--top`, `--format`, `--since`, `--where` and `--errors` -> its stdout equals the invocation with only the final value
  Aspect: `report` reads a valid file with an invalid earlier value and valid final value for each single-valued option -> the command exits with code `0`
  Aspect: `report` receives a valid earlier value and invalid final value for each single-valued option -> stderr contains the validation error for the final value
  Aspect: `report` receives a valid earlier value and invalid final value for each single-valued option -> the command exits with code `2`
  Verification: `tests/test_tally_part3.py::test_last_option_value_only` parameterizes both duplicate directions for all five options, asserting bytes and status; earlier repeated-option tests also remain passing.

### F2 --top

Outcome: The existing category limit continues to select the highest totals with the accepted tie-break, defaults and value validation.

Acceptance criteria:

- F2.AC1: Preserve existing explicit top limits, validation and category ordering.
  Source: "Everything from parts 1 and 2 keeps working exactly as before: when none of the new options below is used, the output, exit codes and error messages are unchanged."
  Kind: regression
  Verification: Existing `tests/test_tally.py::test_f5_ac2_1_top_selection`, `test_f5_ac3_1_top_validation_valid`, `test_f5_ac3_2_top_validation_invalid` and `test_f4_ac3_1_report_ordering` keep passing, together with the JSON ordering assertions in `tests/test_tally_part2.py::test_f3_ac2_1_json_shape_order_and_limit`.

### F3 --format

Outcome: Existing text and JSON report formats retain their byte-level contracts outside summary mode.

Acceptance criteria:

- F3.AC1: Preserve accepted format validation, text output, JSON output and empty-report behavior.
  Source: "Everything from parts 1 and 2 keeps working exactly as before: when none of the new options below is used, the output, exit codes and error messages are unchanged."
  Kind: regression
  Verification: Existing `tests/test_tally_part2.py::test_f3_ac1_1_text_format_equivalence`, `test_f3_ac2_1_json_shape_order_and_limit`, `test_f3_ac3_1_json_escaping_and_utf8`, `test_f3_ac4_1_empty_format_output`, `test_f3_ac5_1_invalid_format` and `test_f3_ac2_3_json_default_separators` keep passing.

### F4 --since

Outcome: The existing inclusive cutoff retains its validation and combines with the new category filter.

Acceptance criteria:

- F4.AC1: Preserve inclusive date filtering, cutoff validation, validation of amounts before filtering and filtered-empty output.
  Source: "Everything from parts 1 and 2 keeps working exactly as before: when none of the new options below is used, the output, exit codes and error messages are unchanged."
  Kind: regression
  Verification: Existing `tests/test_tally_part2.py::test_f4_ac1_1_inclusive_since`, `test_f4_ac2_1_invalid_since`, `test_f4_ac2_2_valid_calendar_cutoffs`, `test_f4_ac3_1_amount_validation_before_filter` and `test_f4_ac4_1_filtered_empty_report` keep passing. F6.AC1 proves the new combined filter.

### F5 TALLY_TOP

Outcome: The existing environment-based top default retains its precedence and validation.

Acceptance criteria:

- F5.AC1: Preserve environment fallback, the empty-or-absent default and explicit-option precedence.
  Source: "Everything from parts 1 and 2 keeps working exactly as before: when none of the new options below is used, the output, exit codes and error messages are unchanged."
  Kind: regression
  Verification: Existing `tests/test_tally_part2.py::test_f5_ac1_1_environment_top_valid`, `test_f5_ac2_1_explicit_top_overrides_environment`, `test_f5_ac3_1_environment_top_invalid` and `test_f5_ac4_1_environment_top_empty_or_absent` keep passing.

### F6 --where category expressions

Outcome: A validated Boolean expression filters exact category names, independently of formatting and in conjunction with the inclusive date cutoff.

Acceptance criteria:

- F6.AC1: Keep only rows satisfying the expression and, when supplied, the date cutoff; category comparisons remain case-sensitive.
  Source: "`--where EXPR` keeps only the rows whose category satisfies the expression `EXPR`."
  Source: "It combines with `--since`: a row is kept when it passes both."
  Source: "A row's category is compared exactly as written, so names are case-sensitive."
  Aspect: `report FILE --where food --format json` supplies `food` as `EXPR` through `--where EXPR` and reads rows categorized `food`, `Food` and `rent` -> stdout contains only the `food` category object
  Aspect: `report FILE --since 2024-03-02 --where food --format json` reads an earlier `food` row of `10`, a cutoff-date `food` row of `2` and a later `rent` row of `3` -> stdout is `[{"category": "food", "total": "2.00"}]\n`
  Verification: `tests/test_tally_part3.py::test_where_exact_identity_and_since` runs both entry-point cases and asserts exact category output and totals.
- F6.AC2: Parse exactly the supplied grammar with ASCII name characters, grouping, recursive negation and the stated operator precedence.
  Source: "An expression follows this grammar:"
  Source: "expr := term ( "|" term )_"
  Source: "term := factor ( "&" factor )_"
  Source: "factor := "!" factor | "(" expr ")" | NAME"
  Source: "`NAME` is one or more of the characters `A-Z`, `a-z`, `0-9` and `_`."
  Source: "A row satisfies `NAME` when its category is exactly `NAME`."
  Source: "(A category with any other character cannot be named; it is matched only through `!`.)"
  Source: "`|` means or, `&` means and, `!` means not, and parentheses group."
  Source: "`!` binds tighter than `&`, and `&` binds tighter than `|`."
  Source: "No spaces are allowed anywhere in an expression."
  Aspect: `report FILE --where A_z09 --format json` reads a row named `A_z09`, exercising name characters from `A-Z`, `a-z`, `0-9` and `_` -> stdout contains the `A_z09` category object
  Aspect: `report FILE --where food|rent&books --format json` uses `|` and `&` and reads `food`, `rent` and `books` rows -> stdout contains only the `food` category object
  Aspect: `report FILE --where !food&rent --format json` reads `food`, `rent` and `books` rows -> stdout contains only the `rent` category object
  Aspect: `report FILE --where !!food --format json` reads `food` and `rent` rows -> stdout contains only the `food` category object
  Aspect: `report FILE --where (!(food|rent)) --format json` reads `food`, `rent`, `books`, `café` and `food-space` rows -> the reported category list is `["books", "café", "food-space"]` when equal amounts make category sorting decisive
  Aspect: `report FILE --where café` attempts to name a category containing a character outside the `NAME` grammar -> stderr is `error: invalid expression 'café'\n`
  Aspect: `report FILE --where food-space` attempts to name a category containing a character outside the `NAME` grammar -> stderr is `error: invalid expression 'food-space'\n`
  Aspect: `report FILE --where "food |rent"` includes a space within the expression -> stderr is `error: invalid expression 'food |rent'\n`
  Verification: `tests/test_tally_part3.py::test_where_grammar_and_precedence` parameterizes identifiers, operator chains, nested groups, repeated negation, whitespace and unnameable categories, asserting reported categories or exact errors.
- F6.AC3: Produce the three stated expression-example selections.
  Source: "Examples: `food|rent` keeps the rows of `food` and of `rent`; `(food|rent)&books` keeps nothing, because no category is two names at once; `!(food|rent)` keeps every row that is neither."
  Aspect: `report FILE --where food|rent --format json` reads equal-amount `food`, `rent` and `books` rows -> the reported category list is `["food", "rent"]`
  Aspect: `report FILE --where (food|rent)&books --format json` reads `food`, `rent` and `books` rows -> stdout is `[]\n`
  Aspect: `report FILE --where !(food|rent) --format json` reads `food`, `rent` and `books` rows -> the reported category list is `["books"]`
  Example: `food|rent` on those categories -> `["food", "rent"]`
  Example: `(food|rent)&books` on those categories -> `[]`
  Example: `!(food|rent)` on those categories -> `["books"]`
  Verification: `tests/test_tally_part3.py::test_where_documented_examples` asserts each selection through the report CLI.
- F6.AC4: Reject empty or malformed expressions with the exact supplied value, empty stdout and status 2.
  Source: "An empty expression, or any text that does not follow the grammar, prints `error: invalid expression '<value>'` to standard error, with `<value>` exactly as given, prints nothing to standard output, and exits with code `2`."
  Aspect: `report FILE --where` receives an empty value or a malformed expression, including unmatched groups, missing operands, adjacent names, trailing tokens, whitespace or illegal characters -> stderr is `error: invalid expression '<value>'\n` with `<value>` replaced by the unchanged argument
  Aspect: `report FILE --where` receives an empty value or a malformed expression -> stdout is empty
  Aspect: `report FILE --where` receives an empty value or a malformed expression -> the command exits with code `2`
  Example: `report FILE --where ""` -> stderr `error: invalid expression ''\n`, exit code `2`
  Example: `report FILE --where "food||rent"` -> stderr `error: invalid expression 'food||rent'\n`, exit code `2`
  Verification: `tests/test_tally_part3.py::test_invalid_where` parameterizes malformed grammar and exact-value preservation, asserting stderr, stdout and exit status. JSON equivalents are covered by F8 and F9.

### F7 --require required categories

Outcome: Every supplied category is checked against the kept rows without changing the report selection; the first missing category is reported.

Acceptance criteria:

- F7.AC1: Retain every require value in command-line order and require at least one kept row with its exact category name, without filtering report output.
  Source: "`--require` is the exception: it may be given any number of times and every value is used, in command-line order."
  Source: "`--require NAME` asserts that the category `NAME` has at least one row among the rows kept by `--since` and `--where`."
  Source: "It does not change which rows are reported."
  Source: "The value is compared exactly as written, like a category name in part 1."
  Aspect: `report FILE --require food --require books --require food` supplies each exact `NAME` through `--require NAME` and reads kept `food` and `books` rows -> the command exits with code `0`
  Aspect: `report FILE --require food --require books --require food` reads kept `food`, `books` and `rent` rows -> stdout equals the report without any `--require` options
  Aspect: `report FILE --top 1 --require books` reads a kept `books` row below the reported top category -> the command exits with code `0`
  Aspect: `report FILE --require " café "` reads a kept category exactly named `café` -> the command exits with code `0`
  Aspect: `report FILE --require Food` reads only a kept `food` category -> stderr is `error: category 'Food' not found\n`
  Aspect: `report FILE --since 2024-03-03 --where !rent --require books` reads a `books` row before the cutoff and a later `rent` row -> stderr is `error: category 'books' not found\n`
  Aspect: `report FILE --where food --require books` reads `food` and `books` rows, with the `books` row excluded by the expression -> stderr is `error: category 'books' not found\n`
  Verification: `tests/test_tally_part3.py::test_require_kept_rows_and_identity` covers repeated and interleaved requirements, top-independent presence, exact arbitrary category names and each filter excluding a required category.
- F7.AC2: Reject each empty require value without stdout and with status 2.
  Source: "A `--require` value must be a non-empty category name."
  Source: "Otherwise print `error: --require must not be empty` to standard error, nothing to standard output, and exit with code `2`."
  Aspect: `report FILE --require ""` or a repeated `--require` list with an empty element is supplied -> stderr is `error: --require must not be empty\n`
  Aspect: `report FILE` receives an empty `--require` value -> stdout is empty
  Aspect: `report FILE` receives an empty `--require` value -> the command exits with code `2`
  Example: `report FILE --require ""` -> stderr `error: --require must not be empty\n`, exit code `2`
  Verification: `tests/test_tally_part3.py::test_empty_require` parameterizes the empty element's position and asserts all three observables. F10 additionally checks its JSON location.
- F7.AC3: Report only the first absent required category in command-line order with status 1 and empty stdout.
  Source: "If no kept row has category `NAME`, print `error: category '<NAME>' not found` to standard error, nothing to standard output, and exit with code `1`."
  Source: "When several required categories are absent, only the first one in command-line order is reported."
  Aspect: `report FILE --require food --require books --require travel` reads only a kept `food` row -> stderr is `error: category 'books' not found\n`, preserving the absent `<NAME>` from the argument
  Aspect: `report FILE` has an absent required category -> stdout is empty
  Aspect: `report FILE` has an absent required category -> the command exits with code `1`
  Example: Required values `food`, `books`, `travel` with only `food` kept -> stderr `error: category 'books' not found\n`, exit code `1`
  Verification: `tests/test_tally_part3.py::test_first_missing_required_category` covers multiple missing categories, a preceding present category and reversed require order, asserting exact stderr, empty stdout and status.

### F8 --errors text and JSON modes

Outcome: Errors retain their plain text defaults or become precisely serialized JSON errors on stderr, without changing status or producing stdout.

Acceptance criteria:

- F8.AC1: Accept text and json modes; absent mode defaults to the exact plain form for old and new errors.
  Source: "`--errors` is `text` or `json`."
  Source: "The default is `text`: errors are printed exactly as in the earlier parts and above."
  Aspect: `report` triggers each old or new error without an `--errors` option -> stderr equals that error's specified plain form
  Aspect: The same failing `report` request specifies `--errors text` -> stderr equals its default-mode stderr
  Aspect: `report FILE --errors json` reads a valid file -> the command exits with code `0`
  Example: `report FILE --where "" --errors text` -> stderr `error: invalid expression ''\n`
  Verification: `tests/test_tally_part3.py::test_error_text_default_and_explicit` parameterizes option, unreadable-file, amount, empty-require and missing-category errors, comparing exact stderr for omitted and explicit text mode and asserting successful status for a valid JSON-mode request.
- F8.AC2: Serialize every error in JSON mode as one object and a newline, preserving the plain message content, exit status and empty stdout.
  Source: "With `--errors json`, an error is printed to standard error as one JSON object followed by a newline, instead of the `error: ...` text."
  Source: "Standard output stays empty and the exit code is the same as for the text form."
  Source: "The object has the keys `code` and `message`, in that order, and then `location` when the error has one."
  Source: "Use the default separators of Python's `json.dumps` and write non-ASCII characters as they are (`ensure_ascii=False`)."
  Source: "`message` is the text the plain form would print after the leading `error: `, so the plain form `error: line 4: invalid amount 'x'` gives `"message": "line 4: invalid amount 'x'"`."
  Aspect: `report FILE --errors json` triggers an error with a location whose plain representation has the form `error: ...` -> stderr equals one `json.dumps` object using default separators and `ensure_ascii=False`, followed by `\n`, with keys ordered `code`, `message`, `location`
  Aspect: `report FILE --errors json` triggers an error without a location -> stderr equals one object with only the ordered keys `code`, `message`, followed by `\n`
  Aspect: A failing `report` request, including invalid amount `x` on file line `4` with plain stderr `error: line 4: invalid amount 'x'`, is run in text and `json` error modes -> JSON `message` equals the plain stderr message after removing `error: ` and its final newline
  Aspect: A failing `report` request is run with `--errors json` -> stdout is empty
  Aspect: A failing `report` request is run in text and JSON error modes -> both runs have the same exit code
  Aspect: `report FILE --require café --errors json` reads a file without `café` -> stderr is `{"code": "MISSING_CATEGORY", "message": "category 'café' not found", "location": "require[0]"}\n`
  Example: Invalid amount `x` on file line `4` with `--errors json` -> `{"code": "INVALID_AMOUNT", "message": "line 4: invalid amount 'x'", "location": "rows[2]"}\n`
  Verification: `tests/test_tally_part3.py::test_json_error_serialization` compares exact stderr bytes, decoded key order, message text, stdout and exit status across modes, including quotes, backslashes and non-ASCII values and ASCII-configured process streams.
- F8.AC3: Invalid errors mode always produces the specified plain error with status 2 and no stdout.
  Source: "Any other value of `--errors` prints `error: --errors must be text or json` to standard error, always in this plain form, nothing to standard output, and exits with code `2`."
  Aspect: `report FILE --errors` receives a final mode other than `text` or `json`, including the empty value -> stderr is `error: --errors must be text or json\n`
  Aspect: `report FILE --errors` receives an invalid final mode -> stdout is empty
  Aspect: `report FILE --errors` receives an invalid final mode -> the command exits with code `2`
  Example: `report FILE --errors JSON` -> stderr `error: --errors must be text or json\n`, exit code `2`
  Verification: `tests/test_tally_part3.py::test_invalid_errors_mode_always_plain` parameterizes invalid final modes, including one after an earlier `json` mode and competing failures.

### F9 Codes

Outcome: All old and new reported errors have the single semantic code specified by the issue, observable in JSON error mode.

Acceptance criteria:

- F9.AC1: Classify unacceptable requests as INVALID_OPTION, unreadable input as UNREADABLE_FILE, invalid row amounts as INVALID_AMOUNT and absent dependencies on categories as MISSING_CATEGORY.
  Source: "Every error the tool reports (from this part and from parts 1 and 2) is reported with exactly one of these codes, chosen by what the error means:"
  Source: "`INVALID_OPTION`: the request itself is not acceptable as written."
  Source: "`UNREADABLE_FILE`: the input file could not be opened or read."
  Source: "`INVALID_AMOUNT`: a row of the input has an amount that is not valid."
  Source: "`MISSING_CATEGORY`: a category the request depends on is absent from the data."
  Aspect: `report` with `--errors json` has an unsupported invocation, invalid top or environment top, invalid format, invalid cutoff, invalid expression or empty require value -> the error object's `code` is `INVALID_OPTION`
  Aspect: `report FILE --errors json` encounters an open failure, read failure or UTF-8 decoding failure -> the error object's `code` is `UNREADABLE_FILE`
  Aspect: `report FILE --errors json` reads a row with an invalid amount -> the error object's `code` is `INVALID_AMOUNT`
  Aspect: `report FILE --require books --errors json` reads no kept `books` row -> the error object's `code` is `MISSING_CATEGORY`
  Example: An invalid `--top` request in JSON mode -> `INVALID_OPTION`
  Example: A missing input file in JSON mode -> `UNREADABLE_FILE`
  Example: A row amount `x` in JSON mode -> `INVALID_AMOUNT`
  Example: An absent required category in JSON mode -> `MISSING_CATEGORY`
  Verification: `tests/test_tally_part3.py::test_error_code_classification` parameterizes every existing error family and each new one, asserting the single code field; monitored entry-point tests inject open and mid-read failures.

### F10 Location

Outcome: JSON errors identify the precise row or require argument responsible using its original zero-based position, omitting location for errors without a list element.

Acceptance criteria:

- F10.AC1: Include zero-based locations for row and require-element errors, counted in the original list rather than filtered or reported data.
  Source: "An error caused by one element of a list carries a `location` that names the element by its zero-based position in its list, written `<list>[<index>]`."
  Source: "The lists are `rows` (the data rows of the file in file order, not counting the header) and `require` (the `--require` values in command-line order)."
  Source: "For example, an invalid amount on file line 4 is the third data row, so its location is `rows[2]`:"
  Source: "{"code": "INVALID_AMOUNT", "message": "line 4: invalid amount 'x'", "location": "rows[2]"}"
  Aspect: `report FILE --errors json` encounters invalid amount `x` on file line `4`, after two data rows -> the error object's `location` is `rows[2]`, the `rows` form of `<list>[<index>]`
  Aspect: `report FILE --since 2024-03-03 --where food --errors json` encounters an invalid amount in the third original data row, even though earlier rows are filtered out -> the error object's `location` is `rows[2]`
  Aspect: `report FILE --require food --require "" --require books --errors json` has an empty second require value -> the error object's `location` is `require[1]`, the `require` form of `<list>[<index>]`
  Aspect: `report FILE --require food --require books --require travel --errors json` reads only a kept `food` row -> the error object's `location` is `require[1]`
  Example: Invalid amount `x` on file line `4` -> `{"code": "INVALID_AMOUNT", "message": "line 4: invalid amount 'x'", "location": "rows[2]"}`
  Verification: `tests/test_tally_part3.py::test_error_list_locations` parameterizes original row positions, filtered rows, empty require positions and absent require positions, asserting literal locations and the supplied line-4 object.
- F10.AC2: Omit the location key entirely for errors not caused by a row or require element.
  Source: "An error that is not caused by one element of such a list has no `location` key."
  Aspect: `report` with `--errors json` encounters an invalid top, invalid format, invalid cutoff, invalid expression, unsupported invocation or unreadable file -> the error object has no `location` key
  Verification: `tests/test_tally_part3.py::test_errors_without_location` checks key lists for each non-list error family.

### F11 --summary

Outcome: The flag replaces the normal report with complete kept-row counts and totals plus the existing limited category array.

Acceptance criteria:

- F11.AC1: Treat summary as a valueless flag and replace normal output with one JSON object describing only kept rows, with exactly the specified ordered keys and serialization.
  Source: "`--summary` is a flag: it has no value word."
  Source: "`--summary` replaces the normal output with a summary of the rows kept by `--since` and `--where`."
  Source: "Standard output is one JSON object followed by a newline, with exactly the keys `rows`, `categories`, `total` and `top`, in that order:"
  Source: "Use the default separators of `json.dumps` and `ensure_ascii=False`."
  Source: "The exit code is `0`."
  Aspect: `report FILE --summary --where food --since 2024-03-01` reads kept `food` rows and excluded rows -> stdout is a single newline-terminated JSON object whose keys are `["rows", "categories", "total", "top"]`
  Aspect: `report FILE --summary` reads a kept `café` row of `1` -> stdout is `{"rows": 1, "categories": 1, "total": "1.00", "top": [{"category": "café", "total": "1.00"}]}\n`, using default `json.dumps` separators and `ensure_ascii=False`
  Aspect: `report FILE --summary` reads valid data and satisfies all required categories -> the command exits with code `0`
  Example: Summary of a single `café` row with amount `1` -> `{"rows": 1, "categories": 1, "total": "1.00", "top": [{"category": "café", "total": "1.00"}]}\n`
  Verification: `tests/test_tally_part3.py::test_summary_flag_shape_and_encoding` places the valueless flag before and after option pairs, asserts exact bytes, key order and status, and exercises UTF-8 output with ASCII-configured streams.
- F11.AC2: Count all kept rows and distinct categories, sum all their amounts with two decimals, and use the existing ordered, top-limited JSON category array for top.
  Source: "`rows`: the number of kept rows, as a JSON integer."
  Source: "`categories`: the number of distinct categories among the kept rows, as a JSON integer."
  Source: "`total`: the sum of the amounts of all kept rows, as a string with exactly two decimals."
  Source: "`top`: the same array that `--format json` prints (one object per category, in the same order, cut by `--top`)."
  Aspect: `report FILE --summary --top 1` reads three kept rows, two named `food` and one named `books` -> the `rows` value is the JSON integer `3`
  Aspect: The same three-row summary invocation reads those two distinct category names -> the `categories` value is the JSON integer `2`
  Aspect: `report FILE --summary --top 1` reads kept amounts `0.10`, `0.20` and `1` -> the `total` value is the string `1.30`
  Aspect: `report FILE --summary --top 1` reads the same kept rows as a corresponding `--format json --top 1` report -> its `top` value equals the JSON report's array, including object keys and category order
  Aspect: `report FILE --summary --where !rent --since 2024-03-01` reads both kept and excluded rows -> its counts and total equal the counts and exact sum of only the kept rows
  Example: Three kept rows with amounts `0.10`, `0.20`, `1` in two categories -> `{"rows": 3, "categories": 2, "total": "1.30", "top": [{"category": "books", "total": "1.00"}]}` with `--top 1` and `books` holding the `1` row
  Verification: `tests/test_tally_part3.py::test_summary_metrics_and_top` asserts individual metric values and JSON types, exact decimal sums including very large amounts, filtering, tie ordering, explicit and environment limits, and equality of top with the existing JSON report.
- F11.AC3: Empty kept data yields the supplied zero summary rather than no data.
  Source: "If no rows are kept, the object is `{"rows": 0, "categories": 0, "total": "0.00", "top": []}` (not `no data`)."
  Aspect: `report FILE --summary` reads a header-only file or filters out every data row -> stdout is `{"rows": 0, "categories": 0, "total": "0.00", "top": []}\n` instead of `no data`
  Example: A header-only file with `--summary` -> `{"rows": 0, "categories": 0, "total": "0.00", "top": []}\n`
  Verification: `tests/test_tally_part3.py::test_empty_summary` parameterizes header-only, date-filtered-empty and expression-filtered-empty inputs and asserts exact stdout.
- F11.AC4: Continue validating format in summary mode, but valid format choices do not change summary output.
  Source: "In this mode the `--format` value is still validated but has no effect on the output."
  Aspect: `report FILE --summary` runs with absent format, `--format text` and `--format json` on the same input -> all stdout bytes are equal
  Aspect: `report FILE --summary --format xml` is supplied -> stderr is `error: --format must be text or json\n`
  Aspect: `report FILE --summary --format xml` is supplied -> the command exits with code `2`
  Verification: `tests/test_tally_part3.py::test_summary_format_validation` compares outputs for valid format choices and asserts invalid-format failure, including JSON error mode.

### F12 Order of checks

Outcome: Only the first error in the specified semantic check order is emitted; option errors are resolved without opening the input and use the final errors mode.

Acceptance criteria:

- F12.AC1: Apply the complete specified first-error ordering independently of argument order.
  Source: "Only the first error is reported, in this order: `--errors`, the top value (`--top`, or `TALLY_TOP` when `--top` is absent), `--format`, `--since`, `--where`, the `--require` values (each must be non-empty, in command-line order), FILE cannot be read, invalid amounts, and finally the `--require` categories that are absent (in command-line order)."
  Aspect: `report` has failures at multiple stages, with option pairs in varied order -> stderr reports only the earliest failure in `--errors`, `--top` or fallback `TALLY_TOP`, `--format`, `--since`, `--where`, empty `--require` values, unreadable FILE, invalid amounts, missing `--require` categories order
  Aspect: `report` has multiple empty require elements with `--errors json` -> its error `location` identifies the first empty value in command-line order
  Aspect: `report FILE --require books` reads two invalid amounts and no `books` category -> stderr reports only the first invalid amount in file order
  Aspect: `report FILE --where food` reads an invalid amount on an excluded category row -> stderr reports that row's invalid amount
  Aspect: `report FILE --since 2024-03-03 --where food --require books --errors json` has an excluded invalid amount row and missing kept `books` -> its error `code` is `INVALID_AMOUNT`
  Verification: `tests/test_tally_part3.py::test_full_check_precedence` parameterizes competing failures at each consecutive stage and all-stage combinations, including environment top fallback, reversed option order and errors in excluded rows; asserts exact single-error stderr and status.
- F12.AC2: Reject all invalid option values before input reading, and use plain mode only for errors-mode validation itself.
  Source: "Everything before FILE is read is reported without reading it."
  Source: "An `--errors` error is always plain; every later error uses the form chosen by `--errors`."
  Aspect: The monitored `report` entry point receives an invalid final `--errors`, `--top`, `TALLY_TOP`, `--format`, `--since`, `--where` or `--require` value while input opening is forbidden -> the recorded input-open call list is `[]`
  Aspect: `report` receives an invalid final `--errors` mode with competing failures -> stderr is `error: --errors must be text or json\n`
  Aspect: `report` receives final `--errors json` and a later-stage error -> stderr is the specified JSON error object followed by `\n`
  Aspect: `report` receives final `--errors text` and a later-stage error -> stderr is the specified plain error text followed by `\n`
  Verification: `tests/test_tally_part3.py::test_options_checked_before_open_and_error_form` uses the existing monitored runpy entry-point style with a patched input opener and asserts no reads, exact error form, empty stdout and correct status for each stage.

### F13 Worked example

Outcome: The issue's sample produces exactly its summary and missing-category JSON error through the report command.

Use this fixture verbatim in the two automated example tests:

```csv
date,category,amount
2024-03-01,food,12.50
2024-03-02,food,7.25
2024-03-02,books,4.5
2024-03-05,travel,19.75
2024-03-06,rent,800
```

Acceptance criteria:

- F13.AC1: The sample summary reports four rows, three categories, the full kept total and the specified two-category tie order with successful status.
  Source: "Given `sample.csv`:"
  Source: "date,category,amount"
  Source: "2024-03-01,food,12.50"
  Source: "2024-03-02,food,7.25"
  Source: "2024-03-02,books,4.5"
  Source: "2024-03-05,travel,19.75"
  Source: "2024-03-06,rent,800"
  Source: "Running `report sample.csv --where "!rent" --require books --summary --top 2` prints (exit code `0`):"
  Source: "{"rows": 4, "categories": 3, "total": "44.00", "top": [{"category": "food", "total": "19.75"}, {"category": "travel", "total": "19.75"}]}"
  Aspect: `report sample.csv --where "!rent" --require books --summary --top 2` reads the issue's sample fixture -> stdout is `{"rows": 4, "categories": 3, "total": "44.00", "top": [{"category": "food", "total": "19.75"}, {"category": "travel", "total": "19.75"}]}\n`
  Aspect: The same worked-example command reads that fixture -> the command exits with code `0`
  Example: `report sample.csv --where "!rent" --require books --summary --top 2` -> `{"rows": 4, "categories": 3, "total": "44.00", "top": [{"category": "food", "total": "19.75"}, {"category": "travel", "total": "19.75"}]}\n`
  Verification: `tests/test_tally_part3.py::test_worked_summary_example` writes the exact fixture and asserts literal stdout and exit status.
- F13.AC2: The sample with the later cutoff fails because books has no kept row, producing the supplied located JSON error and no stdout.
  Source: "Running `report sample.csv --since 2024-03-03 --require books --errors json` prints nothing to standard output and, to standard error (exit code `1`):"
  Source: "{"code": "MISSING_CATEGORY", "message": "category 'books' not found", "location": "require[0]"}"
  Aspect: `report sample.csv --since 2024-03-03 --require books --errors json` reads the issue's sample fixture -> stdout is empty
  Aspect: The same worked-example command reads that fixture -> stderr is `{"code": "MISSING_CATEGORY", "message": "category 'books' not found", "location": "require[0]"}\n`
  Aspect: The same worked-example command reads that fixture -> the command exits with code `1`
  Example: `report sample.csv --since 2024-03-03 --require books --errors json` -> stderr `{"code": "MISSING_CATEGORY", "message": "category 'books' not found", "location": "require[0]"}\n`, exit code `1`
  Verification: `tests/test_tally_part3.py::test_worked_missing_category_example` writes the exact fixture and asserts literal stderr, empty stdout and status.

## Decisions and assumptions

- Keep the existing `tally.py` public command entry point and extend the existing parsing, reading and rendering flow. Internal helper names and types may change; no second report implementation or runtime dependency is needed. (source: rung 1: `tally.py:8`, `tally.py:73`, `tally.py:90`, `pyproject.toml:5`.)
- Preserve Python 3.12, standard-library-only execution, no network or clock accesses, the exact static imports `re`, `sys` and `from decimal ...`, and Decimal arithmetic without binary floats. Import any additional standard-library facilities through the accepted runtime-import pattern so earlier AST tests remain passing. (source: rung 1: `tests/test_tally.py:71`, `tests/test_tally.py:98`, `tests/test_tally.py:137`, `tests/test_tally.py:249`, `tally.py:110`.)
- Preserve original category text, UTF-8 input/output, three-column CSV layout, an initial header, unvalidated row dates and lexicographic inclusive date comparisons. Do not introduce a stricter row-date grammar or trim category values. Preserve the existing supported CSV input domain. (source: rung 1: `tests/test_tally.py:149`, `tests/test_tally.py:167`, `tests/test_tally.py:266`, `tests/test_tally.py:290`, `tally.py:76`, `tally.py:79`, `tally.py:82`, `tests/test_tally_part2.py:189`.)
- Preserve top as ASCII decimal digits denoting 1 through 10 inclusive, accepting leading zeros; an absent explicit top uses a nonempty `TALLY_TOP`, otherwise 3. Preserve exact top and environment error wording. Sort by descending exact category total with Python string ordering ascending for ties, and slice only the category array. (source: rung 1: `tally.py:36`, `tally.py:107`, `tests/test_tally.py:316`, `tests/test_tally.py:401`, `tests/test_tally.py:431`, `tests/test_tally_part2.py:436`, `tests/test_tally_part2.py:477`, `tests/test_tally_part2.py:530`, `tests/test_tally_part2.py:540`.)
- Keep earlier malformed-invocation and unknown-option usage text `usage: tally.py report FILE [--top N]` despite the expanded accepted usage; invalid invocations still exit 2. No old test is retired. (source: rung 1: `tests/test_tally.py:558`, `tests/test_tally.py:568`; rung 2: "Everything from parts 1 and 2 keeps working exactly as before: when none of the new options below is used, the output, exit codes and error messages are unchanged.")
- Collect option/value words before semantic validation. A missing value at the end is the empty string, preserving the existing parser convention; validate it only if it is the final single-valued value, or any require value. A supplied following word is consumed as the value, as in the existing parser. (source: rung 1: `tally.py:21`, `tally.py:28`, `tests/test_tally.py:565`; rung 2: "If one of them is given more than once, only its last value is used (and only that one is checked).")
- Treat repeated `--summary` flags as idempotent. This is an assumption because repetition is specified only for valued options. Reverse by changing flag multiplicity handling and its test if a later requirement specifies rejection. (source: rung 5: `default`.)
- Resolve and validate the final errors mode even when the remaining invocation is structurally invalid; after errors-mode validation, reject unsupported command structure before checking its semantic option values. Render that usage error using the chosen mode with `INVALID_OPTION` and no location. This is an assumption for syntax failures, which the semantic precedence list does not enumerate; reverse by changing structural-validation placement and its precedence tests. (source: rung 1: `tests/test_tally.py:558`, `tally.py:10`, `tally.py:19`; rung 2: "An `--errors` error is always plain; every later error uses the form chosen by `--errors`.")
- Use a complete grammar parse, not Python evaluation; require the entire expression to be consumed. No normalization, spaces or non-ASCII name characters are accepted. The simplest implementation is a small recursive-descent parser or equivalent validated representation; any internal depth safeguard must not impose a new accepted-input limit. (source: rung 2: "An expression follows this grammar:", "No spaces are allowed anywhere in an expression.", "An empty expression, or any text that does not follow the grammar, prints `error: invalid expression '<value>'` to standard error, with `<value>` exactly as given, prints nothing to standard output, and exits with code `2`.".)
- Require values use the existing unrestricted exact category identity and reject only the empty string; they do not use the expression NAME grammar. Whitespace, punctuation and non-ASCII names are valid require values when they exactly match a kept category. Presence uses kept rows before top truncation, including zero-amount rows. (source: rung 1: `tests/test_tally.py:266`, `tests/test_tally.py:176`; rung 2: "`--require NAME` asserts that the category `NAME` has at least one row among the rows kept by `--since` and `--where`.", "The value is compared exactly as written, like a category name in part 1.", "A `--require` value must be a non-empty category name.".)
- An empty require value is `INVALID_OPTION` with `require[index]`; an absent required category is `MISSING_CATEGORY` with `require[index]`. Other option errors have no location; invalid amounts carry their original row position. Text mode adds no code or location text. (source: rung 2: "`INVALID_OPTION`: the request itself is not acceptable as written.", "`MISSING_CATEGORY`: a category the request depends on is absent from the data.", "An error caused by one element of a list carries a `location` that names the element by its zero-based position in its list, written `<list>[<index>]`.", "The default is `text`: errors are printed exactly as in the earlier parts and above.".)
- Treat open, read and UTF-8 decoding failures as unreadable-file errors with the existing `cannot read <path>` message and status 1, without location. Preserve the first invalid amount's existing line-numbered message and status 1, validating all original data rows even when a filter excludes them. (source: rung 1: `tally.py:100`, `tests/test_tally.py:470`, `tests/test_tally.py:482`, `tests/test_tally.py:503`, `tests/test_tally.py:526`, `tests/test_tally_part2.py:273`; rung 2: "`UNREADABLE_FILE`: the input file could not be opened or read.")
- Complete input reading before choosing an invalid-amount or missing-category error so a file-read failure takes precedence. For an input that becomes unreadable after an earlier invalid amount, prioritize unreadability; this collision is not covered by earlier tests, and this reading of the order is an assumption. The simplest implementation can buffer input in memory without an invented size threshold. Reverse by changing read-versus-validation staging and the collision test if a later requirement narrows unreadability precedence. (source: rung 2: "Only the first error is reported, in this order: `--errors`, the top value (`--top`, or `TALLY_TOP` when `--top` is absent), `--format`, `--since`, `--where`, the `--require` values (each must be non-empty, in command-line order), FILE cannot be read, invalid amounts, and finally the `--require` categories that are absent (in command-line order).".)
- Summary counts and overall total cover all kept rows, not only displayed categories. Extend the established precision-aware Decimal summation to the overall total, including very large amounts, and format every total to two decimals. Reuse the existing JSON category-array construction for summary top. (source: rung 1: `tally.py:84`, `tally.py:85`, `tests/test_tally.py:244`, `tests/test_tally_part2.py:101`; rung 2: "`total`: the sum of the amounts of all kept rows, as a string with exactly two decimals.", "`top`: the same array that `--format json` prints (one object per category, in the same order, cut by `--top`).".)
- Emit new JSON stdout and stderr as UTF-8 independently of process stream encoding, following the existing stdout byte-writing convention; compare exact serialized bytes to verify default separators, key order, Unicode and trailing newline. The stderr encoding choice extends an existing stdout pattern and is an assumption; reverse its stream-writing mechanism if future guidance changes the output encoding contract. (source: rung 1: `tally.py:125`, `tests/test_tally.py:290`, `tests/test_tally_part2.py:130`; rung 2: "Use the default separators of Python's `json.dumps` and write non-ASCII characters as they are (`ensure_ascii=False`).".)
- Add new automated tests only under root `tests/`, in `tests/test_tally_part3.py`, following the subprocess and monitored runpy patterns. Each aspect needs mapped assertions, each child process needs `timeout=10`, and any custom child environment extends `os.environ`. Keep all existing tests, exercise every changed production line, and pass ruff, strict mypy and the repository gate without suppressions, skipped tests or weakened configurations. (source: rung 1: `tests/test_tally.py:26`, `tests/test_tally.py:37`, `tests/test_tally_part2.py:32`, `AGENTS.md:14`, `AGENTS.md:20`, `AGENTS.md:23`, `AGENTS.md:30`, `AGENTS.md:72`.)
- Include read-versus-invalid-amount collision, zero-amount required-category presence, repeated summary flags and malformed invocation with JSON mode in the relevant mapped new tests because they verify the decisions above. These are implementation verification details rather than additional issue acceptance criteria. (source: rung 1: `AGENTS.md:23`; rung 2: "Only the first error is reported, in this order: `--errors`, the top value (`--top`, or `TALLY_TOP` when `--top` is absent), `--format`, `--since`, `--where`, the `--require` values (each must be non-empty, in command-line order), FILE cannot be read, invalid amounts, and finally the `--require` categories that are absent (in command-line order).".)
- This intake changes only `.factory-x/feature-definition.md`; the builder implements and verifies the change separately. Do not create branches, push, commit or open a pull request in this fixture. (source: rung 2: "This is a fixture workspace for a benchmark task: there is no branch, no remote
  and no pull request, so do not branch or push anything.".)
