# tally, part 3: category expressions, required categories, coded errors, summary

Extend the `report` command from parts 1 and 2. Everything from parts 1 and 2 keeps working exactly as before: when none of the new options below is used, the output, exit codes and error messages are unchanged.

The full usage is now:

```
python tally.py report FILE [--top N] [--format FORMAT] [--since DATE] [--where EXPR] [--require NAME]... [--errors MODE] [--summary]
```

The options from part 2 and `--where` and `--errors` are written as the option name followed by a separate value word, and may appear in any order. If one of them is given more than once, only its last value is used (and only that one is checked). `--require` is the exception: it may be given any number of times and every value is used, in command-line order. `--summary` is a flag: it has no value word.

## `--where`

`--where EXPR` keeps only the rows whose category satisfies the expression `EXPR`. It combines with `--since`: a row is kept when it passes both. A row's category is compared exactly as written, so names are case-sensitive.

An expression follows this grammar:

```
expr   := term ( "|" term )*
term   := factor ( "&" factor )*
factor := "!" factor | "(" expr ")" | NAME
```

- `NAME` is one or more of the characters `A-Z`, `a-z`, `0-9` and `_`. A row satisfies `NAME` when its category is exactly `NAME`. (A category with any other character cannot be named; it is matched only through `!`.)
- `|` means or, `&` means and, `!` means not, and parentheses group. `!` binds tighter than `&`, and `&` binds tighter than `|`.
- No spaces are allowed anywhere in an expression.

Examples: `food|rent` keeps the rows of `food` and of `rent`; `(food|rent)&books` keeps nothing, because no category is two names at once; `!(food|rent)` keeps every row that is neither.

An empty expression, or any text that does not follow the grammar, prints `error: invalid expression '<value>'` to standard error, with `<value>` exactly as given, prints nothing to standard output, and exits with code `2`.

## `--require`

`--require NAME` asserts that the category `NAME` has at least one row among the rows kept by `--since` and `--where`. It does not change which rows are reported. The value is compared exactly as written, like a category name in part 1.

A `--require` value must be a non-empty category name. Otherwise print `error: --require must not be empty` to standard error, nothing to standard output, and exit with code `2`.

If no kept row has category `NAME`, print `error: category '<NAME>' not found` to standard error, nothing to standard output, and exit with code `1`. When several required categories are absent, only the first one in command-line order is reported.

## `--errors`

`--errors` is `text` or `json`. The default is `text`: errors are printed exactly as in the earlier parts and above.

With `--errors json`, an error is printed to standard error as one JSON object followed by a newline, instead of the `error: ...` text. Standard output stays empty and the exit code is the same as for the text form. The object has the keys `code` and `message`, in that order, and then `location` when the error has one. Use the default separators of Python's `json.dumps` and write non-ASCII characters as they are (`ensure_ascii=False`). `message` is the text the plain form would print after the leading `error: `, so the plain form `error: line 4: invalid amount 'x'` gives `"message": "line 4: invalid amount 'x'"`.

Any other value of `--errors` prints `error: --errors must be text or json` to standard error, always in this plain form, nothing to standard output, and exits with code `2`.

### Codes

Every error the tool reports (from this part and from parts 1 and 2) is reported with exactly one of these codes, chosen by what the error means:

- `INVALID_OPTION`: the request itself is not acceptable as written.
- `UNREADABLE_FILE`: the input file could not be opened or read.
- `INVALID_AMOUNT`: a row of the input has an amount that is not valid.
- `MISSING_CATEGORY`: a category the request depends on is absent from the data.

### Location

An error caused by one element of a list carries a `location` that names the element by its zero-based position in its list, written `<list>[<index>]`. The lists are `rows` (the data rows of the file in file order, not counting the header) and `require` (the `--require` values in command-line order). For example, an invalid amount on file line 4 is the third data row, so its location is `rows[2]`:

```
{"code": "INVALID_AMOUNT", "message": "line 4: invalid amount 'x'", "location": "rows[2]"}
```

An error that is not caused by one element of such a list has no `location` key.

## `--summary`

`--summary` replaces the normal output with a summary of the rows kept by `--since` and `--where`. Standard output is one JSON object followed by a newline, with exactly the keys `rows`, `categories`, `total` and `top`, in that order:

- `rows`: the number of kept rows, as a JSON integer.
- `categories`: the number of distinct categories among the kept rows, as a JSON integer.
- `total`: the sum of the amounts of all kept rows, as a string with exactly two decimals.
- `top`: the same array that `--format json` prints (one object per category, in the same order, cut by `--top`).

Use the default separators of `json.dumps` and `ensure_ascii=False`. If no rows are kept, the object is `{"rows": 0, "categories": 0, "total": "0.00", "top": []}` (not `no data`). In this mode the `--format` value is still validated but has no effect on the output. The exit code is `0`.

## Order of checks

Only the first error is reported, in this order: `--errors`, the top value (`--top`, or `TALLY_TOP` when `--top` is absent), `--format`, `--since`, `--where`, the `--require` values (each must be non-empty, in command-line order), FILE cannot be read, invalid amounts, and finally the `--require` categories that are absent (in command-line order). Everything before FILE is read is reported without reading it. An `--errors` error is always plain; every later error uses the form chosen by `--errors`.

## Worked example

Given `sample.csv`:

```
date,category,amount
2024-03-01,food,12.50
2024-03-02,food,7.25
2024-03-02,books,4.5
2024-03-05,travel,19.75
2024-03-06,rent,800
```

Running `report sample.csv --where "!rent" --require books --summary --top 2` prints (exit code `0`):

```
{"rows": 4, "categories": 3, "total": "44.00", "top": [{"category": "food", "total": "19.75"}, {"category": "travel", "total": "19.75"}]}
```

Running `report sample.csv --since 2024-03-03 --require books --errors json` prints nothing to standard output and, to standard error (exit code `1`):

```
{"code": "MISSING_CATEGORY", "message": "category 'books' not found", "location": "require[0]"}
```
