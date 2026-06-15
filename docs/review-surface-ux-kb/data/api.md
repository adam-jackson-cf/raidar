---
type: API
title: API endpoints
description: Every route served by the zero-dependency node:http server — read endpoints over the projections plus annotation CRUD.
resource: ../../../review-surface/server.mjs
tags: [data, api, server]
timestamp: 2026-06-15T00:00:00Z
---

# API endpoints

`server.mjs` is a zero-dependency `node:http` server. It serves the projection
files as read-only JSON and the SPA static assets, plus annotation CRUD.

## Read endpoints

| Method · Route | Returns |
|---|---|
| `GET /api/runs` | `RunRecord[]` index → [Runs list](../pages/runs-index.md), pills |
| `GET /api/experiments` | `{ experiments[], revision_diffs[] }` → [Experiments](../pages/experiments.md) |
| `GET /api/runs/detail/:id` | `{ run, spans, annotations }` → [Run detail](../pages/run-detail.md) |
| `GET /api/runs/:id/outline` | summary: `span_type_counts`, `tool_calls.by_name[]`, `errors[]`, annotations |
| `GET /api/runs/:id/search` | `{ matches[], truncated }` over span payloads — params `pattern`, `regex`, `case_sensitive`, `max_matches`, `context_chars` → [Search](../components/search-panel.md) |
| `GET /api/spans/:id/payload` | paginated single-span payload — params `target` (input/output), `max_chars`, `offset` |
| `GET /api/annotations?run_id=` | merged raidar + user `Annotation[]` |

## Write endpoints

| Method · Route | Body → Result |
|---|---|
| `POST /api/annotations` | `{ run_id, span_id?, kind, note }` → created `user` annotation (`id` `user-<uuid>`, note ≤4000 chars) |
| `DELETE /api/annotations/:id` | `{ ok: true }` — only `user-`prefixed annotations; Raidar findings immutable |

## Static serving

`/` → `index.html`; `/<path>` → file from `dist/` if present, else SPA fallback
to `index.html`. The client wrapper is
[`src/api/client.ts`](../../../review-surface/src/api/client.ts). Writes land in
[owned data](./owned-data.md).
