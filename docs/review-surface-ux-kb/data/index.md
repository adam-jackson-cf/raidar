---
type: Data Index
title: Data lineage
description: How Raidar benchmark artifacts become the projections, API, and owned data behind the Review Surface.
tags: [data, lineage, architecture]
timestamp: 2026-06-15T00:00:00Z
---

# Data lineage

Everything the surface shows is a **regenerable projection** of authoritative
Raidar artifacts. The only data the surface owns is reviewer annotations.

```
experiments/benchmarks/**            (authoritative Raidar artifacts)
        │  scripts/build-review-data.mjs
        ▼
data/runs.json · data/runs/<id>.json · data/experiments.json   (projections)
        │  server.mjs (node:http, zero deps)
        ▼
/api/* endpoints
        │
        ▼
React SPA (the pages & components in this bundle)
```

| Doc | Covers |
|---|---|
| [Source artifacts](./source-artifacts.md) | What authoritative files are read, and the fields pulled from each |
| [Projections](./projections.md) | The three projection files and the transformations that build them |
| [API](./api.md) | Every endpoint `server.mjs` serves |
| [Owned data](./owned-data.md) | User annotations — the surface's only writable state |

Build commands (from repo root): `make review-surface-data` (project) →
`make review-surface-build` → `make review-surface-serve`.
