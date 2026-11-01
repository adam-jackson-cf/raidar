## First, define the feature

Before you change anything, build the feature definition for the issue below with the
`define` command, seal it, then build against it. You write no file for the definition
and change nothing in the repository while defining: the tool checks the definition and
writes `/app/.raidar/feature-definition.md` from it when you stop.

Read the issue and the repository you are in. Brownfield: the repository's code, tests
and docs are your evidence. Greenfield: apply the constraints the issue gives and the
project's `CLAUDE.md`/`AGENTS.md`. Build what a builder builds from or, only when a
feature itself is unclear, ask questions.

### The issue, numbered

Cite the issue by unit id; never copy or reword its words.

%%%UNITS%%%

- `U<n>`: a statement of the issue, in order. `*` marks a requirement: every
  `*` unit is cited by at least one criterion. Any other unit may be cited
  where it helps.
- `C<n>`: a line of a fenced code block (a worked example, a sample input or
  output); cite it with the criterion it illustrates.
- `A<n>`: an answer from an earlier round; it counts as the issue.

The tool writes each cited unit's words verbatim as the criterion's
`Source:` lines; where a criterion's text and its units differ, the units win.

### What to call, and when

1. `define put`, once, with everything you decided: the block below on standard
   input through a quoted heredoc, which the shell passes through untouched, so
   JSON, quotes, backticks and braces in a value need no escaping:

       define put <<'EOF'
       title: ...
       EOF

   (`define put FILE` reads the block from a file instead.) It applies every
   line it can read, and prints what is wrong, by item: `F2.AC1 line 5: ...`
   for a line it could not read, and the tool's checks of the definition for
   the rest.
2. `define put` again with only the items to fix. A `put` replaces each item it
   names by its id (a question's id is the feature it blocks and its `kind`),
   adds new ones and keeps the rest, so repeating an item is
   safe and a fix is a few lines, never the whole definition. `define show`
   prints the definition as it stands, in the same lines; `define check` lists
   what is wrong with it; `define drop <id>` removes `F2` (with its criteria),
   `F2.AC1`, `decision 3`, `named <name>` or `question F2 <kind>`; a `drop` that
   names nothing lists what the draft holds.
3. `define done ready` when the definition is complete, or `define done ask`
   when you ask questions instead. It refuses while anything blocks, and says
   what. A definition that is not sealed with `define done` when you stop is
   sent back to you once, then the definition is unusable.

Define comes first: `define done ready` precedes every `git commit`. Once it is
sealed, build the issue from `/app/.raidar/feature-definition.md`.

### The block

One item per line, `key: value`, the value as written to the end of the line:
no quotes, no escaping, no indentation. A line that opens an item is followed
by the lines that belong to it.

    title: <the end state in a few words>
    objective: <what the change achieves and why, in a sentence or two>
    named: <a feature, operation, endpoint, option, control or section the issue names>
    from: issue U3 U4 | code path:line | tests path:line | none
    feature F1: <name>
    outcome: <what it does>
    risk: requires review              (leave out otherwise)
    ac F1.AC1: <the criterion, one line in your words>
    source: U3 U4                      (the ids of the units it derives from)
    aspect: <situation> -> <outcome>   (one per situation a test sets up)
    example: <input> -> <expected>     (where the issue gives a value)
    kind: regression                   (only for earlier behaviour that must keep working: then no aspect or example)
    decision 1: <a decided detail>
    rung: 1 to 5
    source: U3                         (leave out when none)
    ref: path:line                     (one per line; leave out when none)
    reverse: <how to reverse an assumption>   (leave out when none)
    risk: requires review              (leave out otherwise)
    retire: <test id as the runner names it>
    source: U3
    reason: <why it stops passing>
    question F1: <what blocks the feature>
    kind: objective_unclear | feature_conflict | missing_input
    proposal: <one answer>
    option: <another answer>           (one per line)

In `aspect:` and `example:` the first `->` outside backticks splits the two
sides; text between backticks is literal, so a `->` inside backticks is part of
the value. A feature `F<n>` has at least one criterion, ids `F1.AC1`, … under
its feature, features in build order.

### What to put in it

List every feature, operation, endpoint, option, control or section the issue
names with `named`, with where its behaviour is stated (`issue`, `code`,
`tests`, or `none`) and its evidence (unit ids for `issue`, `path:line` for
`code` or `tests`, nothing for `none`). The tool checks the evidence; a
feature whose evidence fails counts as `none`, and a `none` feature is an
`objective_unclear` question about it.

Ready: `define done ready`, no `question` lines, and the definition: the
`title`, the `objective`, the features with their criteria and the decisions and
retirements. A decision's `ref` is `path:line`; a retirement's test is as the
runner names it.

Needs input: `define done ask`, one to three `question` lines, each naming the
feature it blocks; its `proposal` is one answer and its `option` lines do not
restate the question. Leave the definition out. Your questions are answered
with their proposals, and you are asked to define again with the answers added
to the units as `A<n>`.

Every value is one line. What you build is judgement only, never a restatement
of the issue, the repository or the rules; write no `Verification:` line and no
test name.

`define put` and `define check` list these problems, by item: an id that is not
`F<n>` or `<feature id>.AC<n>`, or used twice; a criterion with no unit in
`source`, or a unit id not in the list; a criterion that is not `regression`
with no aspect; an aspect or example with an empty side, with `->` outside
backticks or with unbalanced backticks; an example whose expected side holds no
backticked literal; a `regression` criterion with aspects or examples; a field
that begins with markdown or a label; and a `*` unit no criterion cites. Fix
every one before `define done`; it refuses all but the last, which it lists.
