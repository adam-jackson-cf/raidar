# Scorers and metrics

The [root README](../../README.md#concepts) is the human entrypoint for the
current experiment and assessment workflow. This reference describes its scoring
contract; it replaces the retired scenario-era weighted scorer catalogue.

An experiment declares versioned `assessment.scorers` and optionally one
headline `assessment.metric`. The hidden verifier decides correctness; scorers
observe the stored trial evidence offline and write Assessments into an external
store. Scorers do not compare arms or assign scenario weights. Analysis pairs
arms under the locked analysis plan.

## Current owners

| Owner | Responsibility |
|---|---|
| `orchestrator/src/raidar/scoring/catalog/` | Built-in scorer registrations, outcome definitions and manipulation checks |
| `scoring/correctness/` | Verifier-derived correctness observations |
| `scoring/code_quality/` | C/V/E/H/R/G evidence, eligibility and sensitivity measurements |
| `scoring/resource_usage/` | Tokens, cost, turns and wall time |
| `scoring/process_checks/` | Deterministic trace-based process checks |
| `scoring/gate_effort/` | Gate runs, blocking findings, fix effort and autofix evidence |
| `scoring/framework/metrics.py` | Headline metric definitions and selection |
| `scoring/framework/store.py` | Assessment persistence and replay |

Paths without a full prefix in this table are under `orchestrator/src/raidar/`.
Version pins belong to the experiment lock, not this reference; consult the
experiment's declared references and the built-in catalogue for the current
available releases.

## Evidence and missingness

Each observation carries a status. Unavailable or ineligible evidence is not a
zero. Process checks whose traces cannot decide return `insufficient_evidence`
with an `undetermined: ...` reason. An arm without a gate has `not_applicable`
gate measurements. Code-quality eligibility and the selected headline metric
determine whether a composite is available; metric versions with the
zero-correctness rule can return zero when correctness is zero despite missing
quality evidence.

Assessment identity binds the scorer definition and implementation, configuration,
tool lock and input digests. Offline reassessment changes measurement evidence
without rerunning agents. Replay verifies deterministic results. Jev remains
dormant under the recorded policy; it is not an active scoring dependency.

Use the README's assessment and analysis sections for the supported workflow,
and `make help` for public command discovery. The former scenario scorers such
as `design-to-code` and `resource-efficiency`, scenario-level weights and
`verification.min_quality_score` are not current experiment fields.
