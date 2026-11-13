---
name: "aspect-writing"
description: "USE WHEN writing the aspects, examples and kind of a feature definition's acceptance criteria."
---

## Aspects, examples and kind

Every acceptance criterion carries what a test must set up and assert. The
builder writes a test for each aspect, and the engine asks of each one whether a
mapped test sets up that situation and asserts that outcome. A criterion no
aspect describes is not checked.

- `aspects`: a list of `{situation, outcome}`: one situation a test sets up and
  the one outcome it asserts. The engine numbers them `F1.AC1.A1`,
  `A2`, …. A criterion with several situations or several outcomes gets one entry
  per pair.
- `examples`: a list of `{input, expected}`: one concrete case, its expected
  value in backticks. Write one where the issue gives a worked case or states a
  value (a number, a position, an order, a format, a literal): the expected value
  is the one the issue states, and a test must assert it literally.
- `kind`: `behaviour`, or `regression` for a criterion that only says earlier
  behaviour still works ("existing commands are unchanged", "existing tests still
  pass"), with no aspects or examples. It closes only when the builder maps to
  it an earlier test of the repository, one that passed before the change and
  still passes: use it only for behaviour the repository's earlier tests already
  cover. A new behaviour, or one no earlier
  test covers, gets aspects like any other criterion.

Write each aspect by these rules, which are guidance:

1. **Trace.** Restate a clause of the criterion or of its cited units. Never add
   a complementary case ("valid input is accepted" under a rejection criterion),
   a "non-X" case the units do not name, or a qualifier they do not give.
2. **One observable per outcome.** No "X and Y", no "X; Y", no "X followed by Y":
   write two aspects with the same situation instead.
3. **State the outcome as the value a test asserts,** in the criterion's own
   representation, with every literal in backticks: an exit code, a message, a
   field value, a status. Write "the command exits with code `2`", "not rejected
   with `403`", not "the input is allowed" or "works correctly".
4. **The situation creates everything the outcome refers to.** An outcome about
   data being reverted needs a situation in which data was changed; an outcome
   about a record needs the situation that writes it.
5. **Name the entry point the units state:** the command, the request, the
   rendered template, not an internal function.
6. **Only what the units make observable.** No derived or internal effects
   ("operations are not applied again"): "skipped" is a warning, not a
   side-effect you inferred.
7. **Every code literal the criterion or its units state** (a value in
   backticks) appears in backticks in an aspect or in an example.

The engine puts every field on one line: a line break becomes one space and
spaces inside a line stay as you wrote them. A literal that depends on a line
break is described in words ("two lines"), not written out.

For example, with these units:

```text
U7*: Exit with code 2 and print `unknown plant: <name>` to stderr if the plan names a plant the garden does not have.
U8*: Existing commands are unchanged.
```

the two criteria of feature `F2` are:

```json
[
  {
    "id": "F2.AC1",
    "text": "A plan that names an unknown plant exits with code 2 and prints the plant's name.",
    "sources": ["U7"],
    "kind": "behaviour",
    "aspects": [
      {
        "situation": "`waterctl run` reads a plan that names the plant `fern`, which the garden does not have",
        "outcome": "the command exits with code `2`"
      },
      {
        "situation": "`waterctl run` reads a plan that names the plant `fern`, which the garden does not have",
        "outcome": "stderr contains `unknown plant: fern`"
      }
    ],
    "examples": [
      {
        "input": "`waterctl run` with a plan naming `fern`",
        "expected": "exit code `2`, stderr `unknown plant: fern`"
      }
    ]
  },
  {
    "id": "F2.AC2",
    "text": "Existing commands behave as before.",
    "sources": ["U8"],
    "kind": "regression",
    "aspects": [],
    "examples": []
  }
]
```
