# Select Experiment Target Runbook

## Objective

Select unresolved records for the next controlled experiment and record the
targeting before anything runs.

## Guidance

- Consider records with `open` or `reopened` status whose owner can be changed by a
  component (a `raidar:` component area or a `subject:` repo); never `Measurement` faults.
  A `Measurement` record is routed to its owner, never targeted by a component.
- Rank by impact on the experiment's outcome, recurrence across attempts, the
  size of the arm differential, and whether a component change can move the
  detection signal measurably.
- Prefer few records with clear causal hypotheses. When several records are
  targeted by one candidate condition, say that the experiment cannot attribute
  an effect to one change, and name the follow-up that would.
- Record the experiment id, objective, intervention (the component change),
  expected direction, fixed controls (problem pin, agent, model, effort,
  protocol, scorers, thresholds) and the unchanged resolution criterion.
- Transition each selected record to `targeted` with a dated `status_history`
  entry giving the reason and the experiment (and `target` set), then run
  `make ledger-index && make ledger-check`.
- Targeting does not launch anything: execution is a separate, user-approved
  `make experiment-execute`.
