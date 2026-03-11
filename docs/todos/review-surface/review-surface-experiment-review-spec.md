# Review Surface Experiment Review Spec

This document defines the detail view for one representative experiment. The `Experiment Review` explains why an `AgentSpec` is strong or weak, how the conclusion compares to the benchmark, how stable the result is, and what evidence supports the diagnosis.

## Review Questions

The detail view should answer:

- Why did this representative experiment beat or lose to the benchmark?
- Which weaknesses are localized and which are systemic?
- How stable is the result across repeats?
- What changed since the previous representative experiment?

## Page Structure

The Experiment Review should be organized into six sections, in this order.

### 1. Outcome Header

The header should show:

- `AgentSpec` label
- scenario name and revision
- representative experiment id
- benchmark identity and compatibility state
- absolute status
- benchmark delta summary
- confidence band
- scored-run count and unresolved unscored count
- representative selection reason

The header must separate:

- absolute status
- benchmark comparison
- confidence

These are related, but they are not interchangeable.

### 2. Change Context

This section explains what changed since the previous representative experiment for the same `AgentSpec`.

Show:

- previous representative experiment id
- change category chips
- one-line summary of the most important change
- scenario revision or starter fingerprint changes
- repeat-count or rerun-policy changes when relevant

Rules:

- Do not imply that the current result is purely due to model or prompt changes if the scenario contract changed as well.
- If no previous representative exists, say `No prior representative experiment`.

### 3. Evidence Strip

The first large content block should be scenario-family evidence.

For visual scenarios:

- reference image
- current evidence anchor screenshot
- benchmark evidence anchor screenshot
- current diff versus reference
- region evidence cards beneath the strip

For non-visual scenarios:

- current outcome proof
- benchmark outcome proof
- local evidence cards for requirement or subsystem clusters
- verification and implementation proof blocks

Rules:

- Current and benchmark evidence must sit side by side.
- The evidence strip must appear before prose diagnosis.
- If the benchmark is unavailable, keep the current evidence strip and show the benchmark gap explicitly.

### 4. Diagnosis

This section contains three blocks:

- `Strengths`
- `Weaknesses`
- `Opportunities`

Each diagnosis item must include:

- short finding statement
- affected dimension
- comparator context, when applicable
- evidence reference
- confidence qualifier

Rules:

- `Strengths` and `Weaknesses` should be descriptive, not prescriptive.
- `Opportunities` may be hypothesis-shaped, but must not overclaim.
- Diagnosis items should be ordered by user impact, not by metric id.

### 5. Attribute Comparison

This section compares the current representative experiment with the benchmark across the five canonical dimensions.

Show:

- one radar for current versus benchmark
- exact delta bars or labeled numeric deltas beside the radar
- separate efficiency anchors beside or below the dimension comparison

Rules:

- Radar is acceptable here because the reviewer is comparing one experiment against one benchmark, not scanning a large table.
- Efficiency remains outside the radar.
- If benchmark comparison is unavailable, show the current shape only and suppress directional wording.

### 6. Run Consistency And Supporting Evidence

This section explains how repeatable the representative experiment actually is.

Show:

- run chips or run timeline for all runs in the experiment
- which runs were scored, unscored, valid, or invalid
- dimension spread or variance summary
- outlier runs and why they were outliers
- unresolved unscored runs

Supporting drill-down panels should expose:

- raw acceptance checks
- gate history
- traces
- changed files
- requirement coverage gaps
- raw metric payloads when needed for audit

The default view should summarize, not dump raw JSON.

## Interaction Rules

- Any diagnosis item should jump directly to its supporting evidence.
- Benchmark comparison and self-trend comparison should be separate toggles.
- Region cards or local evidence cards should be clickable and filter the supporting evidence panel.
- The detail view should preserve the board verdict wording, but add the evidence and nuance behind it.

## Visual Hierarchy

The page should visually prioritize:

1. scenario-family evidence
2. diagnosis
3. benchmark comparison
4. consistency and confidence
5. raw supporting evidence

This order matters. Reversing it recreates the current metric-dump problem in a larger layout.

## Homepage Scenario Requirements

For `homepage-implementation`, the detail view should expect:

- full-page reference, current, benchmark, and diff imagery
- region evidence for authored layout areas
- deterministic acceptance misses called out by section
- design-replication strengths and weaknesses phrased in human terms

The homepage scenario is the proving case for this detail view, so the visual evidence strip must be strong before broader generalization.

## What The Detail View Must Not Do

- It must not lead with `composite_score`.
- It must not bury benchmark evidence behind tabs that default to raw metrics.
- It must not merge confidence and absolute status into one badge.
- It must not present opportunities as certainty when confidence is low.
