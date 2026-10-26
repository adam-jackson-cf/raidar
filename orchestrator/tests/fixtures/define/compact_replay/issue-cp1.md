# tally: summarise a transactions CSV

Build a command-line tool, written in Python 3.12 using only the standard library, that summarises a CSV file of transactions by category. It must not use the network or the clock.

You run it as:

```
python tally.py report FILE [--top N]
```

`FILE` is the path of a CSV file. `--top` is optional and is written as two separate words, `--top` then `N`. If `--top` is given more than once, only the last one is used (and only that one is checked).

## Input

FILE is UTF-8. The first line is always the header `date,category,amount`, and it is line 1. Every other line is one transaction with exactly three comma-separated fields in the same order. Fields are never quoted, a category is never empty and never contains a comma, and the file contains no blank lines. Dates are not checked at this stage.

`amount` is valid when it is one or more digits, optionally followed by a point and then one or two digits. So `4.5`, `12.00`, `800` and `007.5` are valid (leading zeros are allowed and `007.5` is the number 7.5), while `1.234`, `abc`, `-3`, `.5`, `4.` and an amount with spaces around it are invalid.

Amounts are exact decimal numbers. Add them as decimals, never as binary floating point: `0.10` plus `0.20` is exactly `0.30`.

## Output

Group the transactions by category and sum the amounts of each category. A category name is used exactly as written: it is case-sensitive, and spaces are part of the name, so `food` and ` food` (leading space) are two different categories, and a space is printed as written. Print one line per category to standard output (encoded as UTF-8):

```
<category>: <total>
```

The total always has exactly two digits after the point (`800` is printed as `800.00`, `007.5` as `7.50`). Each line ends with a newline. Nothing else is written to standard output, and standard error stays empty. The exit code is `0`.

Order the lines by total, highest first. Categories with equal totals are ordered by category name in ascending code-point order (plain string comparison, so `a` comes before `b`, and `B` before `a`).

## `--top N`

`--top N` prints only the first `N` lines of the ordered output. The default is `3`. If there are fewer than `N` categories, all of them are printed.

`N` must consist only of the digits `0` to `9` and have a value from `1` to `10` inclusive. Otherwise the tool prints `error: --top must be between 1 and 10` to standard error, prints nothing to standard output, and exits with code `2`. This is checked before FILE is read, so an invalid `--top` is reported even when FILE does not exist.

## Errors

- If FILE cannot be read (it does not exist, or it is a directory, or anything else prevents reading it), print `error: cannot read <FILE>` to standard error, with `<FILE>` exactly as given on the command line. Print nothing to standard output and exit with code `1`.
- If any row has an invalid amount, print `error: line <n>: invalid amount '<value>'` to standard error, where `<n>` is the 1-based line number in the file of the first such row (the header is line 1) and `<value>` is the amount text exactly as written. Print nothing at all to standard output, even if earlier rows were fine, and exit with code `1`.

## Empty input

If FILE contains only the header line, print `no data` followed by a newline to standard output and exit with code `0`.

## Worked example

Given `sample.csv`:

```
date,category,amount
2024-01-03,food,12.50
2024-01-05,rent,800
2024-01-09,food,7.25
2024-01-11,books,19.75
2024-01-12,travel,19.75
```

Running `report sample.csv` prints (exit code `0`):

```
rent: 800.00
books: 19.75
food: 19.75
```

`food` totals `19.75`, the same as `books` and `travel`, so those three are ordered by name; `rent` is first because `800` is the largest total. Only three lines are printed because the default for `--top` is `3`, so `travel` is cut.
