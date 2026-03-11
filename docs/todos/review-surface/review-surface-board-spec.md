# Review Surface Board Spec

This document defines the interaction and information model for the `Scenario Board`. The board is the fast triage view for one scenario. It is optimized for scanability, comparator clarity, and confidence-aware selection of `AgentSpec`s to inspect next.

## Board Questions

The board should let a reviewer answer these questions quickly:

- Which `AgentSpec`s meet the scenario bar?
- Which `AgentSpec`s are ahead of or behind the benchmark?
- Which conclusions are trustworthy versus provisional?
- Which experiments are worth opening for deeper diagnosis?

## Board Header

The board header should show:

- scenario name and revision
- pinned benchmark `AgentSpec` or `No benchmark pinned`
- representative-result rule summary
- cohort size
- freshness summary, such as latest representative completion time

Optional cohort summary chips may include:

- `AgentSpec`s meeting the scenario bar
- `AgentSpec`s below the bar
- `AgentSpec`s with low-confidence representatives

## Row Model

Each row represents one review identity and one representative experiment.

| Row field | Purpose |
| --- | --- |
| `AgentSpec` label | human-readable harness + model identity |
| representative badge | shows selection rule outcome, such as `x3 scored`, `Low Confidence`, or `Unavailable` |
| absolute status | `Meets Scenario Bar`, `Below Scenario Bar`, or `Unavailable` |
| confidence chip | `High`, `Medium`, `Low`, or `Very Low` |
| benchmark delta summary | compact `Ahead`, `Parity`, `Behind`, `Inconclusive`, or `Unavailable` |
| one-line verdict | plain-language diagnosis |
| primary strength | strongest positive differentiator |
| primary weakness | clearest limiting factor |
| dimension cells | five canonical review dimensions |
| efficiency anchors | duration, tokens, and sample size context |
| open-review action | deep link to the Experiment Review |

`Opportunity` should not live in the default row body. If needed, show it in row expand or the detail view.

## Dimension Representation

The board should use aligned bars or score cells for the five canonical dimensions:

- `Task Fidelity`
- `Scenario Fidelity`
- `Workflow Discipline`
- `Execution Reliability`
- `Confidence`

Rules:

- Use a fixed scale across rows.
- Show benchmark delta separately from the current score.
- Pair each dimension score with a small status color or chip, not with dense prose.
- Do not use a row-level radar.

## Efficiency Anchor Cluster

Efficiency sits outside the dimension cells.

The board should show:

- median duration
- median uncached tokens
- scored-run count
- unresolved unscored count when non-zero

This cluster should be compact and right-aligned so it reads as context rather than as the main verdict surface.

## Sorting Rules

Primary sort modes should be:

1. absolute status
2. benchmark delta on primary dimension
3. confidence
4. scenario fidelity
5. execution reliability
6. duration
7. tokens

Rules:

- Sorting by generated prose is low value and should not be first-class.
- The default sort should favor `Meets Scenario Bar`, then benchmark delta, then confidence.
- Low-confidence results should not float above stronger, stable results merely because one scalar score is marginally higher.

## Filtering Rules

The board should support filters for:

- benchmark delta state
- absolute status
- confidence band
- harness
- model
- scenario revision
- evidence availability gaps

Optional filters may include:

- duration range
- token range
- starter fingerprint compatibility

## Compare Affordance

The board should support a lightweight compare flow.

Rules:

- A reviewer can select up to two rows.
- If only one row is selected, the benchmark should be offered as the default comparison target.
- Compare should open a focused Experiment Review compare mode rather than trying to compress a full diff into the board.
- Benchmark delta and self-trend remain separate compare modes in the detail surface.

## State Handling

### Low Confidence

Low-confidence rows must:

- show a visible confidence chip
- explain the cause on hover or expand, such as low run count or missing region evidence
- weaken benchmark delta wording to `Inconclusive` when required by the scoring spec

### Unavailable

Unavailable rows must:

- remain visible in the cohort
- display why they are unavailable, such as `no scored runs` or `missing scenario-fidelity contract`
- suppress dimension cells that would imply a score exists

### Changed Baseline

If starter fingerprint or scenario revision compatibility is broken versus the benchmark:

- show a `Changed Baseline` warning
- keep the row visible
- suppress direct benchmark claims if compatibility is not valid

## Board Language Rules

- The row verdict must be one sentence.
- The strength and weakness lines must be concrete and evidence-shaped.
- Avoid internal ids such as `execution_validity` or `metric_outcomes`.
- Do not call the board a leaderboard.

## What The Board Must Not Show

- no row-level radar charts
- no raw acceptance-check lists
- no raw trace dumps
- no `composite_score` headline
- no ambiguous strip that mixes benchmark delta and self-trend

The board is for triage. Diagnosis belongs in the Experiment Review.
