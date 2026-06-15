---
type: Persona
title: Agent / harness debugger
description: Works at the deepest layer — the execution trace — to locate where in the delivery process a run went wrong.
tags: [persona, trace, debugging]
timestamp: 2026-06-15T00:00:00Z
persona_id: harness-debugger
lands_on: span-tree
---

# Agent / harness debugger

## Role

Debugs the **delivery process itself**: the agent's command/message/file-edit
stream, the verification gates, and the scoring phases. This persona treats the
run as a trace to be walked, not a score to be read. Span names map 1:1 to the
underlying Raidar trace, so this is the layer where the abstraction stops and
the raw evidence begins.

## Core question

> "Where in the delivery process did it go wrong?"

## Activities they perform

| Activity | Where it happens | What answers it |
|---|---|---|
| Walk the execution tree | [Span tree](../components/span-tree.md) | spans: agent trace, gates, scoring phases |
| See where time went | Duration timeline bars in the tree | `start_time_ms` / `end_time_ms` / `duration_ms` |
| Jump straight to failures | Error-cycle button | spans with `status: ERROR` |
| Expand / collapse the trace | Expand-all / collapse-to-sections buttons | span hierarchy |
| Navigate hands-free | Keyboard (↑↓ move, ←→ fold, Esc clear) | tree focus model |
| Hunt for a command or string | [Search panel](../components/search-panel.md) | substring/regex over span payloads |
| Read a span's full payload | [Span detail](../components/span-detail.md) | `input_payload` / `output_payload`, model, tokens |
| Copy a payload for offline analysis | Copy-payload button | clipboard |
| Annotate a precise failure point | [Annotation create form](../components/annotation-create-form.md) | `user` annotation attached to the span |

## Where the journey ends

This is the deepest layer; there is no further hand-off. A finding here
(e.g. "gate failed because `bun run test` was never invoked") closes the loop —
it either confirms the agent underdelivered or proves the scenario contract is
wrong, feeding the [eval engineer's](./eval-engineer.md) contract decision.

## Design tension to watch

The span tree is the one place the plain-language abstraction is intentionally
dropped: raw span names are shown verbatim because fidelity to the trace
matters more than friendliness here. Search and the error-cycle exist so this
raw layer stays navigable at scale.
