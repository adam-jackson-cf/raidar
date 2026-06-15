---
type: Component
title: JsonView
description: An expandable JSON/object viewer for span payloads, with depth control, collapsible subtrees, and truncation of long strings.
resource: ../../../review-surface/src/components/JsonView.tsx
tags: [component, run-detail, payload, viewer]
timestamp: 2026-06-15T00:00:00Z
appears_on: [run-detail]
---

# JsonView

**Purpose.** Makes structured span payloads explorable rather than a wall of text.

**Question answered.** *What exactly was the input/output of this span?*

**Data.** Any JSON-like value or string to parse, plus a `maxExpand` depth.

**Interactions.** Click arrows/objects to expand/collapse; "more" reveals long
strings; clicking a guide line collapses a subtree.

**Page.** Run detail (inside [span detail](./span-detail.md) input/output sections).
