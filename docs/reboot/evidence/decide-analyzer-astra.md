# Vote: analyzer scope — GPT-6 Astra

## Q1 — Analyzer-like scope

**Decision:** Yes. Apply PYS251–253 to these maintained Python roots, retaining explicit seam exceptions rather than excluding whole subsystems:

- `orchestrator/src/raidar/scoring/**`
- `orchestrator/src/raidar/evaluation/analysis/**`
- `orchestrator/src/raidar/engine/health/**`

These are subsystem enforcement boundaries; not every contained module is pure analysis.

**Reason:** Scorers derive observations from snapshots, records and traces (`scoring/framework/context.py:1–21`). Analysis compares arms and explicitly separates pure computation from loading (`evaluation/analysis/__init__.py:1–5`, `report.py:1–5`, `load.py:1–5`). Health audits independently derive trustworthiness from retained evidence (`engine/health/audit.py:1–30`). The reference README explicitly distinguishes “analyzer-like” scope from class naming and says empty prefixes do not establish inapplicability.

## Q2 — PYQ210/PYQ211

**Decision:** Approve evidence-backed **non-applicability of the nominal analyzer-base and project-analyzer-entrypoint requirements** to the current architecture. Do not invent inheritance or relabel structural conformance as inheritance.

Existing scorer contracts remain `raidar.scoring.framework.context.Scorer` and its `assess(ctx)` method. `CoverageScorer` is an existing specialized template, not a universal approved analyzer base.

**Reason:** `Scorer` is a runtime-checkable `Protocol` (`context.py:113–120`); registry loading checks structural conformance and manifest identity (`framework/registry.py:193–207`). `CodeQualityScorer` implements `assess` without inheriting a base (`code_quality/scorer.py:153–175`). `CoverageScorer` provides inherited `assess` and requires subclasses to implement `read` and `manifest` (`coverage_inputs/scorer.py:47–69`). Analysis and health use functions, not a project-analyzer hierarchy.

The reference quality checker targets `*Analyzer` classes and explicit project-analyzer bases (`check-python-quality.py:415–450`). Forcing these contracts onto scorers would change the architecture, not configure an existing convention. Revisit this exclusion if a nominal analyzer hierarchy is introduced; it does **not** exclude analyzer-like behavior from PYS251–253.

## Q3 — Approved seams and existing violations

**Decision:** Approve these existing boundaries, with permissions limited to their actual responsibilities:

| Module, relative to `orchestrator/src/raidar/` | Approved responsibility |
|---|---|
| `scoring/code_quality/measurement.py` | Subprocess execution and filtered environment acquisition **inside `run_process`**, accessed through `ToolEnvironment`. |
| `scoring/framework/store.py` | Assessment/raw-payload persistence and retrieval. |
| `scoring/framework/trial_inputs.py` | Loading and hashing retained trial inputs. |
| `evaluation/analysis/sources.py` | Reading executed jobs, trial records and experiment locks. |
| `engine/health/base.py` | Evidence-loading boundary represented by `Audit.from_disk` and `read_ledger`. |

Keep other existing direct operations as **finding-level baseline debt**, not package exclusions or automatically approved seams. No blanket approval for `snapshot_tool/**`, all of `measurement.py`, or every module that currently reads a file.

**Reason:** These responsibilities are already explicit in `measurement.py:72–110`, `framework/store.py:1–34`, `framework/trial_inputs.py:1–17`, `analysis/sources.py:1–15`, and `health/base.py:41–86`.

Conversely, direct reads occur inside computational modules: `code_quality/snapshot_tool/graph.py:44,73,173,398`, `coverage_inputs/scorer.py:131–135`, `resource_usage/scorer.py:119,221`, and `health/evidence.py:200–201,363–365`. Their existence establishes debt, not approval.

Baseline findings individually by rule, path and stable finding identity; reject additions and growth without renewed approval. In scorer implementation closures, retain existing code unchanged unless a repair is digest-neutral. `framework/registry.py:1–13,117–134` includes transitive imported source in scorer identity; `contract/source_digest.py:1–14` confirms that imports, annotations and other AST changes affect identity. Do not assume moving I/O to a helper preserves Assessment keys.

## Q4 — Ownership marker

**Decision:** Adopt the existing root `CODEOWNERS` as the PYS253 ownership marker. Require it to exist and resolve an owner for every configured subsystem.

**Reason:** `CODEOWNERS:1–5` explicitly declares canonical ownership and assigns `/orchestrator/` to `@adamjackson`, covering all three roots. No new ownership file, subsystem instruction file or source annotation is necessary. A file’s mere existence should not suffice after its applicable ownership entries disappear.

## Q5 — Risks

**Decision:** Accept the scope with narrow seams and explicit ratchets; reject a nominally green configuration based on empty maps or broad exclusions.

**Reason:**

- **Reference blind spots:** The scalability checker misses operations including `os.environ.items()` used by the actual measurement seam, and its filesystem matcher omits `Path.open`, traversal and metadata operations (`check-python-scalability.py:256–304`). Passing that matcher alone does not prove the full boundary.
- **Mixed responsibilities:** Health contains runtime monitoring as well as audits (`engine/health/liveness.py:1–25`). Baseline current operations rather than misrepresenting every runtime action as pure analysis or granting package-wide permissions.
- **Identity churn:** Refactoring any transitive scorer dependency can change Assessment keys, including dependencies outside `scoring/` (`framework/registry.py:117–134`).
- **Misleading inheritance compliance:** Requiring direct `assess` declarations would reject legitimate inherited implementations in `CoverageScorer`; accepting a Protocol as nominal inheritance would weaken PYQ210’s stated contract.
- **Debt becoming exemption:** Baselines must identify existing findings, not authorize future I/O anywhere in an already noncompliant file.