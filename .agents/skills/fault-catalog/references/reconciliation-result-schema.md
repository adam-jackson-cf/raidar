# Reconciliation Result Schema

## Purpose

The portable result the catalog hands to consumers (the observations report, a
proposal) after every candidate from a job has been through the
[Update Fault Status runbook](update-fault-status-runbook.md). It does not
summarise the experiment or control job execution.

## Result contract

One entry in `reconciled_faults` per failure mode reconciled to an existing or
new record, including a `no change` decision:

| Field | Required value |
|---|---|
| `canonical_id` | The record ID, such as `FCR-003`. |
| `exact_fault_type` | The record's exact canonical wording. |
| `category` | The record's category. |
| `owner` | The record's owner (component, verifier, problem, harness, analysis). |
| `status` | The record's status after reconciliation. |
| `affected` | A list of `{experiment, job, arm, model, reasoning_effort, checkpoints}`. |
| `concise_measured_evidence` | A short measured statement with its per-arm denominators. |
| `record_link` | The repository-relative path (with anchor) of the record. |
| `changed_record_ids` | Every record created or changed; `[]` for no change. |

Candidates whose distinctness stays unresolved go only in the optional
`unresolved_candidates` collection, each with `candidate_evidence`,
`artifact_links` (repository-relative, `path:line` where possible) and `reason`
(the records it was compared with).

## Portable form

```yaml
reconciled_faults:
  - canonical_id: FCR-003
    exact_fault_type: "Exact canonical wording from the record"
    category: Process
    owner: component
    status: targeted
    affected:
      - experiment: python__forge__guided-pilot
        job: forge-02
        arm: guided
        model: openai/gpt-6-luna
        reasoning_effort: low
        checkpoints: [checkpoint_1, checkpoint_2]
    concise_measured_evidence: "pytest missing in 65/128 guided implementer sessions."
    record_link: "docs/reboot/pilot-v2-fault-catalog.md#fcr-003"
    changed_record_ids: [FCR-003]

unresolved_candidates:
  - candidate_evidence: "Measured non-canonical candidate and its scope."
    artifact_links:
      - "jobs/<group>/<experiment>/<job>/trials/<trial>/raidar-oracle/steps/<checkpoint>/grading.log:120"
    reason: "Not distinct from FCR-005 yet; no catalog change."
```

## Consumer rules

- Consumers use only `reconciled_faults` and its `exact_fault_type`; they never
  coin labels or render unresolved candidates as fault types.
- The linked record stays the authority for ID, wording, category and status.
- Catalog changes go only through the Update Fault Status runbook.
