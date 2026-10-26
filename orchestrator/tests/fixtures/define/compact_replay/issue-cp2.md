# tally, part 2: output format, date filter, environment default

Extend the `report` command from part 1. Everything from part 1 keeps working exactly as before: when none of `--format`, `--since` or the `TALLY_TOP` environment variable is used, the output, exit codes and error messages are unchanged.

The full usage is now:

```
python tally.py report FILE [--top N] [--format FORMAT] [--since DATE]
```

Each option is written as the option name followed by a separate value word, and options may appear in any order. As with `--top`, if any option is given more than once, only its last value is used (and only that one is checked).

## `--format`

`--format` is `text` or `json`. The default is `text`, which is the part 1 output; `--format text` given explicitly behaves identically.

With `--format json`, standard output is one line followed by a newline: a JSON array with one object per category, in the same order as the text output and cut by `--top` in the same way. Each object has the keys `category` and `total`, in that order, and `total` is a string with exactly two decimals. Use the separators `, ` between items and `: ` after keys (the default of Python's `json.dumps`), so a line looks like:

```
[{"category": "rent", "total": "800.00"}, {"category": "food", "total": "19.75"}]
```

Strings are escaped as JSON requires (a `"` in a category becomes `\"`), but non-ASCII characters are written as they are, encoded as UTF-8, never as `\uXXXX` escapes. (Python: `ensure_ascii=False`.)

If there are no rows to report, JSON output is `[]` followed by a newline. (Text output prints `no data` in that case, as in part 1.)

Any value other than `text` or `json` prints `error: --format must be text or json` to standard error, nothing to standard output, and exits with code `2`.

## `--since`

`--since DATE` keeps only the rows whose date is on or after DATE. The comparison is inclusive: a row dated exactly DATE is kept. Dates in the file and DATE are written `YYYY-MM-DD`, so they compare as plain strings.

DATE is valid only when it has the form `YYYY-MM-DD` and names a real calendar date (`2024-02-30` and `2024-13-01` are invalid). Otherwise print `error: invalid date '<value>'` to standard error, with `<value>` exactly as given, print nothing to standard output, and exit with code `2`.

Every row's amount is validated (and reported with its file line number, as in part 1) before filtering, so a bad amount in a row that `--since` would drop is still an error, exit code `1`.

If filtering leaves no rows, the empty-input rule applies: text output prints `no data`, JSON output prints `[]`, exit code `0`.

## `TALLY_TOP`

The environment variable `TALLY_TOP` sets the default for `--top`:

- If `--top` is not given and `TALLY_TOP` is set to a non-empty value, that value is used as N, and it must be valid by the same rule as `--top` (only digits, value 1 to 10).
- If `--top` is given, it takes precedence and `TALLY_TOP` is ignored entirely, even when `TALLY_TOP` is invalid.
- If `--top` is not given and `TALLY_TOP` is invalid, print `error: TALLY_TOP must be between 1 and 10` to standard error, nothing to standard output, and exit with code `2`.
- An empty `TALLY_TOP` counts as not set.
- If neither is given, N is `3`.

## Order of checks

Option errors are reported before FILE is read, and only the first error is reported, in this order: the top value (`--top`, or `TALLY_TOP` when `--top` is absent), then `--format`, then `--since`, then FILE cannot be read, then invalid amounts. For example `--format xml --since nope` reports the `--format` error.

## Worked example

Given `sample.csv`:

```
date,category,amount
2024-02-28,rent,800
2024-03-01,food,12.50
2024-03-02,food,7.25
2024-03-02,books,4.5
2024-03-05,travel,19.75
```

Running `report sample.csv --since 2024-03-01 --format json --top 2` prints (exit code `0`):

```
[{"category": "food", "total": "19.75"}, {"category": "travel", "total": "19.75"}]
```

The `rent` row is dated before `2024-03-01`, so it is dropped, while the `food` row dated exactly `2024-03-01` is kept. `food` and `travel` both total `19.75`, so they are ordered by name. `books` (`4.50`) is cut by `--top 2`.
