---
name: "python-conventions"
description: "Guide Python naming, package structure, and code-object choices. USE WHEN writing or refactoring Python code."
---

# Guidance

Complements Ruff and Raidar's Python quality checks; does not replace deterministic gates.

## Names

- Use precise domain names over generic names such as `manager`, `helper`, `utils`, `data`, `thing`, or `processor`.
- Name functions and methods by the action or question they perform.
- Name classes by the role or concept they model, not by implementation mechanics.
- Name protocols by the capability they require.
- Name modules and packages after cohesive responsibilities, not mixed tool buckets.

## Package Structure

- Keep folders grouped by responsibility and import boundary, not by incidental file type.
- Add a new package only when it owns a stable concept, API boundary, or workflow slice.
- Avoid catch-all directories unless Raidar already has a specific established convention for them.
- Keep generated runtime surfaces such as `scenarios/`, `experiments/`, `.tmp/`, and `.cache/` out of canonical source-quality reasoning.

## Object Choice

- Start with a function for stateless behavior.
- Use a class when state and behavior belong together, lifecycle matters, or polymorphism is needed.
- Use a dataclass for structured data carriers with annotated fields.
- Use a Protocol for structural contracts across implementations.
- Use an Enum for a closed symbolic set.
- Do not create a god class to centralize unrelated workflows.

## Raidar Scorer And Runtime Conventions

- Collect project-wide evidence once and pass it through runtime context instead of rediscovering it in each scorer.
- Batch external tool invocations when a tool accepts multiple files.
- Honor scenario, runtime, generated-artifact, and caller-provided exclusions end to end.
- Label proxy evidence explicitly when direct retained evidence is unavailable.
- Prefer small scorer-owned evidence helpers over shared catch-all utility modules.
