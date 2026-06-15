---
type: Component
title: AnnotationCreateForm
description: Always-visible form to record an issue/good/note against the run or a selected span — the only place the surface writes data.
resource: ../../../review-surface/src/components/AnnotationCreateForm.tsx
tags: [component, run-detail, annotations, write]
timestamp: 2026-06-15T00:00:00Z
appears_on: [run-detail]
---

# AnnotationCreateForm

**Purpose.** Lets reviewers capture judgements — the surface's one mutation and
its [only owned data](../data/owned-data.md).

**Question answered.** *How do I record what I noticed about this run or span?*

**Data.** Form state (`kind` ∈ issue/good/note, `note` text) plus an optional
selected `Span` (`id`, `name`) to scope the annotation. Submits via
[`POST /api/annotations`](../data/api.md).

**Interactions.** Kind toggle switches the submit affordance ("Annotate run" /
"Annotate this span"); Cmd+Enter or button submits; "use run instead" detaches
from the span. Created notes appear in [AnnotationCards](./annotation-cards.md)
and are deletable.

**Page.** Run detail (Findings & annotations section).
