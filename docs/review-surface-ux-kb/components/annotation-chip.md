---
type: Component
title: AnnotationChip
description: A compact icon-only badge for an annotation's kind (issue/good/note) and source, shown inline on spans and lists.
resource: ../../../review-surface/src/components/AnnotationChip.tsx
tags: [component, annotations, indicator]
timestamp: 2026-06-15T00:00:00Z
appears_on: [run-detail]
---

# AnnotationChip

**Purpose.** A glanceable marker that an annotation exists, exporting the shared
`KIND_STYLES` (icon/colour/label per kind) reused across the surface.

**Question answered.** *Is there an annotation here, and what kind/source is it?*

**Data.** `Annotation` (`kind`, `source`); note preview in the title.

**Interactions.** Hover for the note preview; otherwise indicator-only.

**Page.** Run detail (span-tree rows, finding chips). `KIND_STYLES` is also
imported by the Experiments page for finding rendering.
