---
type: Component
title: SpanDetail
description: The right-panel deep view of a selected span — type, status, duration, model, tokens, input/output payloads, and attached annotations.
resource: ../../../review-surface/src/components/SpanDetail.tsx
tags: [component, run-detail, spans, payload]
timestamp: 2026-06-15T00:00:00Z
appears_on: [run-detail]
---

# SpanDetail

**Purpose.** The raw evidence panel — everything known about one span.

**Question answered.** *What is the full context of this span: its inputs,
outputs, model, cost, and any annotations?*

**Data.** Selected `Span`: `span_type`, `status`, `duration_ms`, `model`,
`input_tokens` / `output_tokens`, `input_payload` / `output_payload` (rendered
by [`JsonView`](./json-view.md)), plus span-scoped `Annotation[]`.

**Interactions.** "Annotate this span" focuses the
[create form](./annotation-create-form.md); a copy button copies the payload to
the clipboard.

**Page.** Run detail (right, ~40%, shown when a span is selected).
