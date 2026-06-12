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

| Persona | Objective | Where they land |
|---|---|---|
| Benchmark reviewer / platform lead | Which AgentSpec delivers this scenario better, and can I trust the sample? | **Experiments** page: per-scenario AgentSpec comparison (best-first with Δ-vs-best), scenario context chips/description, validity, sample adequacy, unscored warnings, cost/duration, finding counts, and status-bearing run pills. |
| Scenario / eval engineer | Why did this run score what it scored, and is the scenario contract right? | **Run detail**: scorecard breakdown (scorer → weighted metric contributions with pass/fail and evidence), gate status chips, findings as evidence-linked annotations, requirements/metric evidence spans. |
| Agent / harness debugger | Where in the delivery process did it go wrong? | **Span tree** (agent execution, gates, scoring phases) with expand/collapse-all, error cycling, keyboard navigation (↑↓/←→/esc), per-run evidence search with match highlighting, payload drilldown with copy. |

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
```

Inside `review-surface/` for development: `npm run dev` (Vite on 5951, proxies
`/api` to the server on 5950).

Synthetic fixtures are always labeled — experiment ids, run ids, and payloads
carry `synthetic` markers and the UI shows a `SYNTHETIC FIXTURE` badge. They
must never be read as real benchmark evidence.

## Attribution

UI patterns and selected component code are adapted from
[raindrop-ai/workshop](https://github.com/raindrop-ai/workshop) (MIT). See
`LICENSE-THIRD-PARTY.md`. Workshop's daemon, database, replay, and live-trace
machinery are intentionally not used: Raidar execution stays behind the repo's
public `make ...` workflows.
