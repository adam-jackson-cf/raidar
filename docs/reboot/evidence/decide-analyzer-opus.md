# Vote: analyzer scope — Claude Opus 5.5

## Q1. Analyzer-like code

**Decision:** Yes. The analyzer-like scope is one root: `orchestrator/src/raidar/scoring/`. It includes every scorer package, `catalog/`, `framework/`, `code_quality/snapshot_tool/` and `judge/`. Nothing outside it is in scope: `engine/health/`, `evaluation/analysis/`, `grading/`, `raidar/judge/`, `lab/stages/**` and `orchestrator/tests/` are all excluded. Match the scope with `path_matches_prefix` (trailing slash), not the reference's bare `startswith`.

**Reason:**
- **What "analyzer" means in the catalogue.** In the reference, an analyzer is a code-analysis plugin. `check-python-quality.py:415-450` checks classes whose names end in `*Analyzer` against approved bases and a project entrypoint. `check-python-scalability.py:85-140` expects analysis code to do no direct file, subprocess or environment access.
- **Raidar's matching code is the scorer stack.**
  - The `Scorer` protocol is the plugin contract: `scoring/framework/context.py:113-144`, `assess(ctx)`.
  - `registry.py:193-197` enforces that contract when a scorer loads.
  - The project-inspecting code is `code_quality/snapshot_tool/{symbols,imports,graph,files}.py` (tree-sitter/radon over agent code), plus `judge/{rubric,codebase,symbols}.py`.
- **The exclusions are not this kind of code:**
  - `engine/health`: engine hooks whose job is reading and writing trial records. The raidar package `AGENTS.md` says "Health reads records only". It writes reports (`liveness.py:410`, `reaper.py:128`, `records_manifest.py:57`) and reads environment config (`liveness.py:83`). The reference detector finds 25 file-I/O hits there; the whole package would have to be a seam, so the rule would enforce nothing.
  - `evaluation/analysis`: statistics over Assessments, with no plugin base or entrypoint. All 3 of its file reads are already in `sources.py`.
  - `lab/stages/**`: released, frozen `stage-tree/1` packages. Stage tools exist to do I/O; for example, `verify_tool/checks.py:81` runs the checks as subprocesses.
  - `grading/`: part of the grading identity closure. It runs oracle tests and does not analyse code.
  - `raidar/judge/`: an HTTP client. It contains the environment seam `client.py:168 api_key_from_env`.

## Q2. PYQ210 / PYQ211

**Decision:** Both rules apply.
- **Analyzer classes:** in scope, classes named `*Scorer` (Raidar's analyzer suffix) or `*Analyzer`.
- **PYQ210 approved bases:** `Scorer`, `RawPayloadScorer`, `AwaitingAnswersScorer` (`framework/context.py`) and `CoverageScorer` (`coverage_inputs/scorer.py:47`, the abstract template).
- **Exempt from PYQ210:** the definitions of the approved bases themselves.
- **PYQ211 entrypoint map:** `{Scorer: assess, RawPayloadScorer: assess_with_raw, AwaitingAnswersScorer: awaiting_answers, CoverageScorer: read}`.
- **Exempt from PYQ211:** classes that are themselves `Protocol` declarations.

**Reason:**
- `assess(ctx) -> AssessResult` is the single entry that the registry and rescore call.
- `CoverageScorer.read` is an abstract template hook. `DefinitionCoverageScorer` (`definition_coverage/scorer.py:53`) and `TestCoverageScorer` (`test_coverage/scorer.py:50`) both define it, so PYQ211 has **0** current findings.
- PYQ210 has **5** current findings. These scorers satisfy `Scorer` only by structure and don't inherit it by name:
  - `CodeQualityScorer`
  - `CorrectnessScorer`
  - `DeterministicProcessComplianceScorer`
  - `ResourceUsageScorer`
  - `VerificationEffortScorer`
- Adding the base changes the python-ast/1 `implementation_digest` of `code-quality@9`, `correctness@10`, `process-compliance@12`, `resource-usage@8` and `verification-effort@1`. All are in `.omp/identity.json`. So these 5 are ratcheted, not repaired.
- Keeping only the `*Analyzer` suffix would make the rule vacuous, because no such class exists.

## Q3. PYS251 / PYS252 seams and existing violations

**Decision:** Approve seams as a path → function map in the same shape as the reference's `SEAM_SUBPROCESS_WRAPPERS`. Use module-level approval only for modules whose single job is loading or persisting data.

- **Subprocess and environment seam:** `code_quality/measurement.py::run_process`. This is the only `subprocess.run` and the only `os.environ` read in scope. It is injected as `ToolEnvironment.runner`. Credentials stay in `raidar/judge/client.py` (out of scope).
- **File I/O seams, whole module:**
  - `framework/{atif,inventory,store,trial_inputs,validation,registry,rescore,rescore_config}.py` — the SDK input and store boundary, 12 hits
  - `coverage_inputs/records.py`, `verification_effort/records.py` — record readers, per their docstrings
  - `judge/{audit,ledger,cache,policies,rubric_questions,label_export}.py` — Jev stores and loaders
- **File I/O seam, function only:** `judge/rubric.py::_read_snapshot` (reads the snapshot once).
- **Framework modules not on the list stay in scope.** That covers both new ones and the pure `context`, `manifest`, `metrics`, `result`, `ownership` and `keys` modules.

**Repair now (digest-neutral):**
- `judge/promotion.py:412,428`: move the policy-file read and write into `policies.py`, which already owns `load_policy`.
- Nothing in `scoring/judge` is in any scorer's digest closure: it is imported only by itself, `cli/jev*.py` and `evaluation/review/quality`.

**Ratchet (identity-bound) through the merged `scripts/checks/check-finding-baseline.py`:**
- Use a new tool name with `.omp/baselines/<tool>.json` (shrink-only). The checker currently accepts only `ruff` and `mypy`, so it needs a parser for the new checker's JSON output.
- 21 file-I/O findings:
  - `code_quality/{diffs,evidence,parity,quality,transition}.py` (5)
  - `snapshot_tool/*` (8)
  - `coverage_inputs/{scorer,testsource}.py` (2)
  - `process_checks/{assess×2,outputs,stall}.py` (4)
  - `resource_usage/scorer.py` (2)
- The 5 PYQ210 findings from Q2.
- PYS252 has **0** findings.
- Counts come from an AST scan using the reference detector's logic.

**Widen the detector:** also catch any `os.environ` load (the reference misses `.items()`), `Path.open`, `os.open`, and every `subprocess.*` call. A scan with this wider set found new hits only in approved seams: `framework/store.py` (2) and `judge/audit.py` (1). It adds no baseline entries.

## Q4. PYS253 ownership marker

**Decision:** The marker is the existing `orchestrator/src/raidar/scoring/AGENTS.md`.

**Reason:**
- Per-subsystem `AGENTS.md` is the repo's existing convention: `contract/`, `evaluation/review/`, `design/experiment/` and `scoring/` each have one.
- The scoring one already states the subsystem's binding rules: relock on implementation change, offline scorers, `insufficient_evidence` semantics.
- Owner identity comes from `CODEOWNERS` `/orchestrator/ @adamjackson`.
- The check should require `<scope root>/AGENTS.md` for each scope root, not the reference's "any marker exists anywhere".
- It is not Python and not under `lab/stages`, so it has no identity impact.

## Q5. Risks

1. **Baseline debt stays until each scorer's next `@N+1`.** Every repair relocks all tracked experiments (`scoring/AGENTS.md`). Batch the PYQ210 base additions and the snapshot_tool cleanup into those releases.
2. **snapshot_tool runs out of process.** It runs under `python -I` with the pinned interpreter (`measurement.py:40-45`) and cannot import raidar seams. Its seam must live inside snapshot_tool: one per-file source reader, shipped with `code-quality@10`. Today `files`, `symbols`, `graph` and `imports` each read the same file again; that repeated reading is the real scalability debt.
3. **Approved seams are permanent exemptions.** Keep whole-module approval only for single-purpose I/O modules, and fail closed for new modules.
4. **The reference code is not safe to copy unchanged:**
   - `startswith` without a trailing slash matches sibling paths.
   - The environment and file-open detection is incomplete.
   - PYS253 passes if any one marker exists anywhere.
   - PYQ211 would flag the `Protocol` extension classes unless they are exempted.
5. **Nominal inheritance from a `runtime_checkable` Protocol.** Making `@dataclass`/`__init__` scorers inherit it should be safe on Python ≥3.12 (an inference, not tested). It still needs a test when the repairs land.
6. **The judge repair needs proof it is digest-neutral.** Prove it with `make identity-check` against an unchanged `.omp/identity.json`, plus a test of the `jev promote` path. Jev is dormant, so the path is rarely exercised.
7. **The exclusions need owner approval recorded in the coverage ledger.** If plugin-style analyzers later appear in `evaluation/analysis` (0 findings, `sources.py` as its seam), widen the scope and add an `AGENTS.md` there.