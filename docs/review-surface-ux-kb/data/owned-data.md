---
type: Data Source
title: Owned data — user annotations
description: The one piece of state the surface owns and writes — reviewer annotations stored in data/user-annotations.json.
resource: ../../../review-surface/server.mjs
tags: [data, owned, annotations, write]
timestamp: 2026-06-15T00:00:00Z
---

# Owned data — user annotations

Everything else the surface shows is regenerable from
[source artifacts](./source-artifacts.md). **User annotations are the sole
exception** — the only data the surface creates and owns.

## Storage

`review-surface/data/user-annotations.json` — a flat array. Each record:

| Field | Value |
|---|---|
| `id` | `user-<uuid>` |
| `run_id`, `span_id?` | references into the projection |
| `kind` | issue / good / note (reviewer-chosen) |
| `note` | text, ≤ 4000 chars |
| `source` | `"user"` |
| `created_at` | epoch ms at POST |
| `category` | fixed `"annotation"` |
| `evidence` | `[]` (user notes carry no evidence refs) |

## Lifecycle

- **Write** — `POST /api/annotations` appends and persists the whole array
  (`fs.writeFileSync`).
- **Delete** — `DELETE /api/annotations/:id` filters and re-persists; only
  `user-`prefixed ids are deletable.
- **Read/merge** — at read time, user annotations are concatenated with the
  projected Raidar findings for the run.

## Caveat

File-based with no locking — concurrent writers can lose data; the surface is a
single-reviewer local tool, not a multi-client service. Written via the
[create form](../components/annotation-create-form.md), surfaced in
[annotation cards](../components/annotation-cards.md).
