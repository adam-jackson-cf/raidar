# Update Fault Status Runbook

## Objective

Reconcile job evidence against the catalog by making exactly one decision for
each evidenced failure mode: create a record, update an existing record, reopen
a resolved record, or make no catalog change.

## Preconditions

- Read the catalog, every potentially matching record and the
  [Fault Category Record schema](fault-category-record-schema.md).
- Use persisted job artifacts as evidence ([evidence map](raidar-evidence-map.md)).
  An observations report may point to candidates but cannot justify a creation
  or a status change on its own.
- Establish the configuration from the lock: experiment id, `lock_sha256`, job
  id, problem pin, agent name and version, model, reasoning effort, arm and
  condition digest, progression mode, scorers and headline metric.
- Compare like with like: the same progression view, the same checkpoint scope,
  the same lock thresholds. Never compare a strict-view measure with a
  continue-view one, or case pass rate with solved share.
- Do not change a status when required evidence, the original resolution
  criterion or the comparison baseline is unavailable.

## Create or update

### Update an existing record

Update a record when the new evidence has the same failure mode, the same (or an
equivalent) detection signal, the same affected invariant, and the same
remediation target. A different model, arm, condition, checkpoint, severity or
failed case is a new occurrence of the same category unless the invariant and
the remediation target differ materially.

1. Append one observed-configuration row per newly evidenced configuration.
2. Add the exact artifact paths and measured result, per arm.
3. Append a dated history entry.
4. Apply the status rules below.
5. Update the index row only when its status or current target changes.

### Create a record

Create a record only when every gate passes:

1. **Artifact:** persisted job evidence shows the failure; a hypothesis does not.
2. **Category:** a reusable failure mode, not one verifier case, exception text,
   model, checkpoint or number.
3. **Distinctness:** no existing record has the same invariant and remediation
   target.
4. **Detection:** an observable signal later jobs can evaluate the same way.
5. **Evidence:** at least one exact configuration and measured example.
6. **Impact:** a concrete correctness, regression, integration, recovery,
   structural, process or measurement-validity consequence.
7. **Owner:** the remediation target names who can change it: a component, the
   verifier, the problem, the harness (Raidar) or the analysis.

## Status rules

Status describes the evidence about the category, not whether one case passes.

- **Keep `open`** when a job adds an occurrence without targeting the category,
  when the result is worse, unchanged or incomparable, or when an intervention
  ran without a pre-registered criterion.
- **`targeted`** (from `open` or `reopened`) only before execution, once the
  experiment id, the intervention, the unchanged resolution criterion and its
  scope (arms, attempts, checkpoints) are recorded. A locked, unexecuted
  experiment qualifies; selecting it is not mitigation.
- **`mitigated`** (from `targeted`) when the intervention ran, comparable
  evidence shows the detection signal improved, but the full criterion did not
  pass or its scope is incomplete, with no material regression elsewhere.
- **`resolved`** (from `targeted` or `mitigated`) only when the criterion existed
  before the validating job and was not weakened, the intervention is recorded
  under Solution, every attempt and checkpoint in scope completed, the full
  threshold is met, the detection signal is absent in scope, the fault did not
  move into another behaviour, and the validation artifacts are recorded.
- **`reopened`** (from `resolved`) when a later job reproduces the same failure
  mode with an equivalent signal and contradicts the resolution claim. A missing
  artifact, an infra exclusion or an incomparable configuration does not reopen.
- **`not-reproducible`** (from `open`) when a controlled follow-up recreates the
  lock (problem pin, agent, model, effort, condition, progression, seed) and the
  fault does not recur.
- **`routed`** (from `open`) for a `Measurement` record whose owner is outside
  the component space, once the routing (issue, decision item) is recorded. It
  is not a resolution.

## Record the outcome

1. Preserve prior evidence, unsuccessful interventions and history.
2. Record exact artifact paths, not only a report.
3. Record prior status, resulting status, experiment, decision and reason.
4. Update the index and the record together.
5. Keep Harbor's token-priced `cost_usd` (nominal under ChatGPT session auth)
   distinct from billed cost.
6. Never include credentials or secret values.

## Validation

- Every candidate has a `create`, `update`, `reopen` or `no change` decision.
- Every new record passes all seven gates and duplicates no invariant and target.
- Every transition is allowed and meets its conditions.
- Index and records agree on ID, wording, category, status and target.
- Every number points to persisted job artifacts.
