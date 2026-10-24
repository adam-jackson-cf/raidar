# Head-to-head quality signals (Task 0.7)

Input for the WS-K K2 spec (Quality element, "the head-to-head checkpoint scatter"). Traces requirement F14.

Source read (read-only): `S/src/slop_code/dashboard/pages/head_to_head_quality.py` (203 lines), where
`S = /Users/adamjackson/Projects/inflight/slop-code-bench`. Every other path below is relative to `S/src/slop_code/`.

## Verdict

Confirmed. The page shows **two signals and nothing else**. Both are checkpoint-level A-vs-B scatters: one for lint, one for complexity.

| # | Panel (card header / figure title) | Graph id | Metric column | Axis label |
|---|---|---|---|---|
| 1 | "Lint Errors Comparison" / "Lint Errors per Checkpoint" | `h2h-lint-scatter` | `lint_errors` | `Errors` |
| 2 | "Complexity Comparison" / "Complexity per Checkpoint" | `h2h-complexity-scatter` | `cc_high_count` | `Rating` (mislabelled: the value is a count, see N2) |

The page has no tables, summary statistics, deltas, win/loss counts, per-LOC normalisation or trend lines.

## Shared scatter mechanics (`make_quality_scatter_checkpoint_level`)

- **Inputs:** Dash inputs `h2h-run-a` and `h2h-run-b`. Each is a run directory path, chosen in the head-to-head selector (`dashboard/settings_content.py:513`; options are filled in `dashboard/settings_callbacks.py:331`).
- **Data load:** `build_context([run_a, run_b])` → `build_chart_context` → `load_run(run_dir)` (`dashboard/data.py:557`).
  - `load_run` reads **`<run_dir>/checkpoint_results.jsonl`**, which is `CHECKPOINT_RESULTS_FILENAME` in `common/constants.py:24`. Each line becomes one row, merged with the config metadata plus `run_path`.
  - `load_run` also requires a verified score summary (`load_verified_score_summary`). When that is missing, it drops the whole run and the page shows empty figures.
- **Point identity:** one point per `(problem, idx)` pair.
  - `idx` is the checkpoint `order` from the problem config (`entrypoints/evaluation/metrics.py:~211`). When there is no `idx` column, the page falls back to `checkpoint`.
  - The page inner-joins run A and run B on `["problem", idx]`, so only checkpoints present in both runs are plotted.
  - Before the join, it drops rows with a null metric (`dropna(subset=[col])`).
- **Axes and hover:**
  - x = run A value, y = run B value.
  - A dashed y = x parity line runs from 0 to `max(max_a, max_b)` (forced to 1 when both maxima are 0). Axis titles are `"<run name> (<axis label>)"`.
  - The hover text shows the problem, the checkpoint idx, and both raw values.
  - All points use one colour (`#663399`); there is no colour encoding.
- **Run names:** `get_display_annotation(first row of the run).split(" - ")[0]`. This gives the model name, a thinking suffix, and agent/prompt extras when those are not the default.
- **Direction:** lower is better for both metrics. A point below the diagonal means run B is better at that checkpoint.

## Signal 1: `lint_errors`

- **Data file chain:**
  1. The dashboard reads `checkpoint_results.jsonl`, key `lint_errors`.
  2. That key is written by `create_problem_reports` → `get_checkpoint_metrics` → `get_quality_metrics` → `_build_metrics_from_snapshot` (`metrics/checkpoint/extractors.py:128`), as `lint["errors"]`.
  3. `lint["errors"]` comes from `<checkpoint_dir>/quality_analysis/overall_quality.json`, field `lint.errors`. Constants: `QUALITY_DIR`, `QUALITY_METRIC_SAVENAME` (`common/constants.py:14,19`).
- **Computation:**
  - **Snapshot level:** the sum of `file_metric.lint.errors` over every measured file in the checkpoint snapshot (`metrics/driver.py:436-490`).
    - "Measured files" means files whose extension matches the entry file's language, excluding venv, `.git`, `node_modules`, `.tox` and `.nox`.
    - If **any** file's lint is unavailable, the snapshot value becomes `errors=None` (`available=False`, `unavailable_reason="lint_unavailable_in_snapshot"`). The scatter then drops that checkpoint.
  - **File level:** `metrics/languages/python/lint_metrics.py::calculate_lint_metrics` runs `uv run ruff check --statistics --output-format json --isolated --preview` with `--select` set to every code in `RUFF_CODES` (`metrics/languages/python/constants.py`). That is roughly 85 selectors, including `F`, `B`, `S`, `C90`, `PLR`, `ERA`, `SIM`, `RUF`, `TRY` and `PGH`.
    - The value is the sum of `count` over all rule statistics. It is a raw count of findings, **not** weighted.
    - The weights in `RUFF_CODES` (1–4) are not used by this metric.
    - Ruff exit codes: 0 or 1 is valid. Any other exit code, or malformed output, marks the metric unavailable.
- **Units:** an absolute count of findings per checkpoint snapshot. It is not normalised by LOC. A per-LOC variant, `lint_per_loc`, exists in the same row but this page does not use it.

## Signal 2: `cc_high_count`

- **Data file chain:**
  1. The dashboard reads `checkpoint_results.jsonl`, key `cc_high_count`.
  2. That key is written by `_build_metrics_from_snapshot` (`metrics/checkpoint/extractors.py:143`) as `functions["cc_high_count"]`.
  3. `functions["cc_high_count"]` comes from `quality_analysis/overall_quality.json`, field `functions.cc_high_count` (model: `metrics/models.py:302`).
- **Computation:** `metrics/driver.py::_compute_function_stats` sets `cc_high_count = len([c for c in cc_values if c > HIGH_CC_THRESHOLD])`, where `HIGH_CC_THRESHOLD = 10` (`metrics/driver.py:166`).
  - `cc_values` holds the `complexity` of every symbol of type `function` or `method` across all measured files (`metrics/driver.py:~366`).
  - Per-symbol complexity is a tree-sitter cyclomatic count (`metrics/languages/python/symbols.py:33,681`). It is `1 + (number of nodes in COMPLEXITY_NODE_TYPES within the symbol)`:
    - the node types are `if`/`elif`/`for`/`while`/`except`/`assert`, list/set/dict comprehensions, generator expressions, `boolean_operator`, `conditional_expression`, and comprehension `if_clause`;
    - functions and methods include the base 1; classes do not.
  - This is **not** radon CC. Radon is only used for raw line metrics and MI.
- **Units:** the number of functions or methods with CC > 10 per checkpoint snapshot. It is an absolute count and is not normalised.

## Finalised signal list for K2

| Signal | Source metric (exact) | Computation | Data file | Point grain | Better |
|---|---|---|---|---|---|
| H2H lint scatter | `lint_errors` | Σ ruff finding counts over measured files (fixed selector set, unweighted); null if any file's lint is unavailable | `checkpoint_results.jsonl` ← `quality_analysis/overall_quality.json` `.lint.errors` | (problem, checkpoint idx), inner join A∩B | lower |
| H2H complexity scatter | `cc_high_count` | count of function/method symbols with tree-sitter CC > 10 | `checkpoint_results.jsonl` ← `quality_analysis/overall_quality.json` `.functions.cc_high_count` | (problem, checkpoint idx), inner join A∩B | lower |

## Notes for the K2 spec

- **N1 — data availability in Raidar.** Raidar has no `checkpoint_results.jsonl`. K2 needs the per-checkpoint equivalents from the ported `code-quality` scorer output: the per-checkpoint lint finding count, and the count of functions with CC > 10.
  - If the ported scorer uses a different lint selector set or complexity engine, the values are not comparable with SCB. The K2 spec should name the engine and threshold explicitly.
- **N2 — axis mislabel.** The "Rating" label on the complexity axis is wrong in SCB; the value is a count. K2 should label it "Functions with CC > 10".
- **N3 — pairing across repeats.** SCB compares one run against one run and has no repeat handling. Raidar arms have repeats, so K2 must choose one of:
  - per-repeat points;
  - a per-(problem, checkpoint) median across repeats (inference: the median is the safer choice for the paired-Δ story in K2).
- **N4 — dropped and unmatched points.** The SCB scatter silently drops checkpoints with null lint and checkpoints missing from either run. K2 should show "n plotted / n excluded (reason)" so the drops are visible.
- **N5 — related SCB pages (not part of this page).** `head_to_head_evolution.py` plots the same two columns, plus `loc`, averaged per progress step. `run_analysis_quality.py` shows single-run distributions, and a `loc` vs `lint_errors` scatter.
  - The K2 item "SCB dashboard distributions (lint density, CC-mass concentration, function LOC)" comes from other pages or metrics (`lint_per_loc`, `cc_concentration`/`cc_top20`, `lines_per_symbol`), not from `head_to_head_quality.py`.
