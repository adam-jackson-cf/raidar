# Propose Next Revision Runbook

## Objective

Turn targeted records into the next revision of an arm, written as a
proposed ab-test (the incumbent arm against the candidate), validated and
locked but not executed.

## Rules

- **Agent and workflow releases only.** Change the agent release, the workflow
  release, or which releases an arm uses. Never change the problem pin, the problem's
  specification or tests, the model or reasoning, the protocol, the
  scorers, the headline metric or the verdict thresholds.
- **One change per record.** Every change names the record it targets, the
  mechanism by which it should move that record's detection signal, and the
  expected direction.
- **Hidden-test firewall.** For every changed line, ask whether a practitioner
  could write it from the specification, the environment and general practice
  without the verifier's tests. Literals, phrasings or behaviours known only
  from hidden tests are excluded, and the exclusion is written down.
- **Incumbent unchanged.** Keep the incumbent's agent and workflow releases as
  registered, so its arm digest equals the earlier
  experiment's. Check it with `make experiment-plan`.
- **Measurement faults are not targets.** Say which records are left to the
  verifier, problem, harness or analysis owner, and what that means for the
  outcome the candidate can move.
- **Process check.** For each process change, name the process-compliance
  check it should move and whether the analysis can detect it under the chosen
  baseline arm.

## Steps

1. Read the targeted records and the incumbent experiment's `experiment.yaml`.
2. Create `lab/experiments/ab-test/<runtime>__<subject>__<intent>/` (a new
   experiment, because the baseline arm is the incumbent arm). Register the
   candidate's agent or workflow release in `lab/releases.yaml`.
3. Write `experiment.yaml` (`schema_version: 3`): arms
   `incumbent` and `candidate`, `baseline_arm: incumbent`, the
   incumbent's assessment and thresholds, and `min_effects` for the metrics the
   candidate is expected to move.
4. Write `rationale.md` beside it: each change with its record, mechanism,
   firewall check and expected signal; what is deliberately not changed; the
   predicted outcome per metric; attribution limits; cost.
5. `make experiment-validate`, `make experiment-lock`, then
   `make experiment-lock LOCK_CHECK=1` and `make experiment-plan` with the same
   `EXPERIMENT=`. Confirm the incumbent's arm digest.
6. Record the proposal as an `M-NNN` remedy (`faults`, `lever`, `home`, `proof_plan`,
   `experiment` = the locked path), then move the targeted faults to `targeted`,
   naming the experiment, and run `make ledger-index && make ledger-check`.
7. Do not run `make experiment-execute`; execution is a separate decision.
