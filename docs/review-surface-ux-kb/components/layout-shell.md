---
type: Component
title: Layout shell
description: The app frame — title, Experiments/Runs nav, and the "Compare · Explain · Trace" subtitle.
resource: ../../../review-surface/src/router.tsx
tags: [component, navigation, layout]
timestamp: 2026-06-15T00:00:00Z
appears_on: [experiments, runs-index, run-detail]
---

# Layout shell

**Purpose.** Wraps every route with a fixed header (brand "Raidar Review",
Experiments/Runs `NavLink`s, and the subtitle *Compare agent specs · Explain
scores · Trace failures*) and an `<Outlet>` for page content.

**Question answered.** *Where am I, and how do I switch between comparing specs
and reading runs?*

**Data.** None — pure chrome. Active-link styling from the router.

**Interactions.** Two nav links toggle `/` ↔ `/runs`. Unmatched routes redirect
to `/`.

**Page.** All. Defined in `router.tsx` alongside `AppRouter`.
