# Raidar Review Surface

A Workshop-inspired review surface over Raidar benchmark evidence. It adapts the
review model of [Raindrop Workshop](https://github.com/raindrop-ai/workshop)
(run list → run outline → span tree → payload drilldown, with `issue | good | note`
annotations) to Raidar's own artifacts: scenario runs, scorecards, gate history,
trace events, scorer/metric evidence, requirements coverage, process metrics,
and deterministic findings.

Raidar artifacts remain authoritative. Everything this surface shows is a
regenerable projection of `experiments/benchmarks/**`; user annotations are the
only data it owns (stored in `data/user-annotations.json`).

## Personas and objectives

Every surface leads with a plain-language verdict and keeps the granular metrics
behind progressive disclosure, so each persona starts zoomed-out and drills in.

| Persona | Objective | Where they land |
|---|---|---|
| Benchmark reviewer / platform lead | Which agent spec delivers this scenario better, did a revision change help, and can I trust the sample? | **Experiments** page: a per-revision verdict headline (best delivery, the gap to the runner-up, and the run worth opening first), then a comparison table framed as delivery verdict (Strong/Solid/Shaky/Failing) · repeatability · issues · sample confidence · pace · tokens. Expanding a spec splits metrics into "where points were lost" vs "what held up" with pass-ratio bars, score-area bars, evidence-linked findings, and run pills. Below: score-vs-run-time scatter, failure-pattern rollups, and revision movement with contract diffs and comparability warnings. |
| Scenario / eval engineer | Why did this run score what it scored, and is the scenario contract right? | **Run detail**: a verdict banner (one-sentence outcome, gates, headline score) over a "why it scored this" scorecard — each score area shows its share of the composite with bars, and each check is click-to-evidence. Findings render as plain-language, evidence-linked annotations. Long ids and source artifacts sit behind a "Technical details" disclosure. |
| Agent / harness debugger | Where in the delivery process did it go wrong? | **Span tree** (agent execution, gates, scoring phases) with a duration timeline, expand/collapse-all, error cycling, keyboard navigation (↑↓/←→/esc), per-run evidence search with match highlighting, and payload drilldown with copy. This is the deepest layer, where raw span names map 1:1 to the trace. |

### Semantic verdict layer

Raw scores, sample sizes, and finding categories are translated into one
consistent good/bad vocabulary in `src/utils/verdict.ts`:

- **Delivery** tiers from the composite score (Strong ≥ 0.9, Solid ≥ 0.75,
  Shaky ≥ 0.5, Failing below) — a presentation band; Raidar has no canonical
  composite grade.
- **Repeatability** maps directly to Raidar's `REPEAT_VARIANCE_STDDEV_THRESHOLD`
  (0.1): "Volatile" means Raidar would raise a repeat-variance finding.
- **Confidence** comes from the sample adequacy flags (minimum/preferred met).
- Finding categories (`failed-gate`, `missing-required-command`, …) render as
  plain-language labels with the raw category retained in tooltips.

## Architecture

```
experiments/benchmarks/**            Raidar benchmark artifacts (authoritative)
        │  scripts/build-review-data.mjs
        ▼
data/runs.json                       run index records
data/runs/<run_id>.json              { run, spans, annotations } projections
data/experiments.json                experiment rollups for comparison
        │  server.mjs (node:http, zero deps)
        ▼
/api/runs · /api/runs/detail/:id · /api/runs/:id/outline · /api/runs/:id/search
/api/spans/:id/payload · /api/annotations (CRUD) · /api/experiments
        │
        ▼
React SPA (Vite + Tailwind), components adapted from Workshop's app
```

Projection mapping (Workshop concept → Raidar source):

| Workshop | Raidar projection |
|---|---|
| run | one benchmark run (`run.json` + scorecard + AgentSpec + scenario revision) |
| span | phase/evidence node: agent trace events (commands, messages, file edits), verification gates, scorer/metric evidence, requirements, validity, process metrics, artifacts |
| annotation | deterministic findings from `findings.json` (source `raidar`, evidence-linked) plus manual reviewer notes (source `user`) |
| run outline | computed summary: span type counts, tool/gate rollup, error shortlist, annotations |
| search | substring/regex search across span payloads |

## Usage

From the repo root (public interface):

```
make benchmark-fixture-synthetic   # optional: labeled synthetic demo data
make review-surface-data           # project experiments/benchmarks into data/
make review-surface-build          # npm install + typecheck + vite build
make review-surface-serve          # serve app + API on http://localhost:5950
make review-surface-test           # end-to-end functional suite (Playwright)
```

Inside `review-surface/` for development: `npm run dev` (Vite on 5951, proxies
`/api` to the server on 5950).

## End-to-end tests

`tests/surface.spec.ts` is a Playwright functional regression net over the
synthetic fixture, asserting every interaction works: navigation and scenario
anchors, experiment expand/collapse and run pills, the comparison headline and
Δ-vs-best framing, the run verdict banner, gate chips, the evidence-linked
scorecard checks, findings jumps, per-run search (plain + regex + result
click), the span tree (error cycle, expand/collapse-all, keyboard nav, escape),
payload copy, annotation create/delete/kind-toggle, the runs sidebar filter and
selection, the tradeoff scatter, failure patterns, revision-movement diffs, and
the failing / passing / **unscored** run states — plus a console-error guard.

`make review-surface-test` projects the fixture, builds the app, ensures the
Chromium browser, and runs the suite via `server.mjs` (Playwright's `webServer`
reuses an already-running server on 5950).

Synthetic fixtures are always labeled — experiment ids, run ids, and payloads
carry `synthetic` markers and the UI shows a `SYNTHETIC FIXTURE` badge. They
must never be read as real benchmark evidence.

## Relationship to the former benchmark-view

The earlier `benchmark-view` dashboard is deprecated and removed. Its
persona-relevant capabilities live here: revision movement deltas, scenario
contract diffs with comparability warnings, failure-pattern rollups, and the
score-vs-duration tradeoff scatter. Its decision-score heuristic, signal
coverage matrix, improvement playbook, and scenario portfolio census were
deliberately not carried forward — the deterministic findings layer and the
comparison tables supersede them.

## Attribution

UI patterns and selected component code are adapted from
[raindrop-ai/workshop](https://github.com/raindrop-ai/workshop) (MIT). See
`LICENSE-THIRD-PARTY.md`. Workshop's daemon, database, replay, and live-trace
machinery are intentionally not used: Raidar execution stays behind the repo's
public `make ...` workflows.
