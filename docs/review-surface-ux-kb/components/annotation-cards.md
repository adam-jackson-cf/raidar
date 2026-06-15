---
type: Component
title: AnnotationCards
description: Renders Raidar findings and user notes as issue/good/note cards with category labels and evidence references that jump to the proving span.
resource: ../../../review-surface/src/components/AnnotationCards.tsx
tags: [component, run-detail, findings, annotations]
timestamp: 2026-06-15T00:00:00Z
appears_on: [run-detail]
---

# AnnotationCards

**Purpose.** Presents both machine findings (`source: raidar`) and human notes
(`source: user`) in one evidence-linked list, issues first.

**Question answered.** *What did Raidar flag and what have reviewers noted — and
where's the proof?*

**Data.** `Annotation[]`: `kind`, `source`, `category`
([labelled](../concepts/finding-categories.md)), `note`, `span_id`, and
`evidence[]` (`source`, `reference`, `detail`). A `spanNameById` map resolves
jump targets.

**Interactions.** "jump:" buttons select the evidence span (`?span=`); user
annotations (`id` prefixed `user-`) show a delete button; Raidar findings are
immutable.

**Page.** Run detail (Findings & annotations section, and within span detail).
