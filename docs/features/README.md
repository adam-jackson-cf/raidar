# Raidar features

One document per user-facing feature. Each names its sub-features and their observable end
state, how a user reaches it, how to drive it deterministically, and its gotchas. Each links the
outcome tests or journeys that prove it.

| Feature | User | Proved by |
| --- | --- | --- |
| [Plan an experiment](plan-an-experiment.md) | Researcher, `raidar` CLI | [plan-experiment journey](../../review-surface/journeys/plan-experiment.journey.mjs) |
| [Assess and analyze results](assess-and-analyze-results.md) | Researcher, `raidar` CLI | [analysis](../../orchestrator/tests/test_analysis_command.py) and [rescore](../../orchestrator/tests/test_rescore_store_bindings.py) outcome tests |
| [Compare experiments](compare-experiments.md) | Benchmark reviewer, review surface | [compare-experiments journey](../../review-surface/journeys/compare-experiments.journey.mjs) |
| [Explain and trace a trial](explain-and-trace-a-trial.md) | Eval engineer, harness debugger, review surface | [trace-and-annotate journey](../../review-surface/journeys/trace-and-annotate.journey.mjs) |

Journeys run through the surface lever, one command that launches an isolated instance, checks
it is usable, drives the journey, writes evidence and cleans up:

```bash
node scripts/surface/lever.mjs --data-dir "$(mktemp -d)/data" --evidence-dir <dir> \
  --journey review-surface/journeys/<name>.journey.mjs
```

The lever exits 3 and prints `RAIDAR-SURFACE-DOCTOR-FAILED` when the instance is not usable;
that is an environment failure, not a product defect.

When a change adds or alters user-facing behaviour, update its feature document and link the
test or journey that proves it. A new feature gets a new document with the same four sections.
