# Tie-break: analyzer scope — GPT-6.1 Sol

## Q1 — Scope

**Chosen:** Astra’s full answer: `orchestrator/src/raidar/scoring/**`, `orchestrator/src/raidar/evaluation/analysis/**`, and `orchestrator/src/raidar/engine/health/**`.

**Reason:** “Analyzer-like” is broader than a code-analysis plugin hierarchy. Analysis explicitly separates pure comparison from loading (`evaluation/analysis/report.py:1–7`); health re-derives trial trustworthiness from retained evidence (`engine/health/audit.py:1–30`). Both fit the behavioral boundary. Mixed runtime responsibilities warrant seams and baseline debt, not exclusion of the analytical subsystem. The reference README separately configures analyzer-like prefixes and analyzer class contracts (`python-reference/README.md:15–23`).

## Q2 — PYQ210 / PYQ211

**Chosen:** Astra’s full answer: evidence-backed non-applicability to the current architecture; retain existing scorer contracts without imposing nominal inheritance.

**Reason:** `Scorer` is explicitly a runtime-checkable `Protocol` (`scoring/framework/context.py:113–120`), and the registry checks structural conformance and matching manifests (`framework/registry.py:193–207`). `CoverageScorer` is a specialized ABC with inherited `assess` and an abstract `read` hook (`coverage_inputs/scorer.py:47–71`).

Opus’s suffix substitution and mandatory protocol inheritance would introduce a new nominal convention, then classify existing structural implementations as five violations. That is an architectural policy change rather than enforcement of the existing contract. Analyzer-like behavior can remain subject to PYS251–253 without a nominal analyzer hierarchy.

## Q3 — Seams

**Chosen:** Astra’s full map and finding-level ratchet policy:

- `scoring/code_quality/measurement.py::run_process`: subprocess and filtered environment acquisition through `ToolEnvironment`.
- `scoring/framework/store.py`: Assessment/raw-payload persistence and retrieval.
- `scoring/framework/trial_inputs.py`: retained-input loading and hashing.
- `evaluation/analysis/sources.py`: executed-job, trial-record and experiment-lock reading.
- `engine/health/base.py`: evidence loading through `Audit.from_disk` and `read_ledger`.
- Other existing direct operations remain individually baselined debt, not implicitly approved seams.

**Reason:** These responsibilities are explicit in the checked implementations and module contracts: `measurement.py:72–110`, `store.py:1–34`, `trial_inputs.py:1–17`, `sources.py:1–15`, and `base.py:41–86`. Conversely, snapshot parsing directly reads files (`snapshot_tool/graph.py:41–47,70–76`), and coverage payload collection reads bytes inside the scorer module (`coverage_inputs/scorer.py:131–135`). Existing I/O does not itself justify an enduring exemption.

The identity constraint is real: scorer digests recursively include imported source (`framework/registry.py:117–134`). Preserve identity-bound code and ratchet findings individually unless a repair is demonstrably digest-neutral.

## Q4 — Ownership marker

**Chosen:** Astra’s full answer: root `CODEOWNERS`, requiring an applicable owner for every configured subsystem.

**Reason:** `CODEOWNERS:1–5` declares canonical ownership, provides a repository-wide owner and explicitly assigns `/orchestrator/` to `@adamjackson`. It therefore covers all three adopted roots without introducing another ownership convention. Mere marker-file existence must not pass after applicable owner entries disappear.

## Q5 — Risks

**Chosen:** Astra’s full answer: accept broad behavioral scope with narrow seams and finding-level ratchets; reject misleading compliance through empty configuration or broad exemptions.

**Reason:** Checked evidence supports its material risks:

- The reference recognizes only `subprocess.run`/`Popen`, limited filesystem methods, and selected environment operations (`check-python-scalability.py:256–304`). Actual approved code uses `os.environ.items()` (`measurement.py:86–89`).
- Health includes analytical and operational responsibilities; inclusion does not assert every contained operation is pure analysis.
- Transitive scorer imports affect identity (`registry.py:117–134`).
- `CoverageScorer` legitimately inherits entrypoint implementations, making direct-method requirements inappropriate (`coverage_inputs/scorer.py:64–71`).
- Baselines must identify existing findings rather than exempt entire noncompliant files.

## Adopted decision

- **Q1:** Astra — all three subsystem roots.
- **Q2:** Astra — PYQ210/211 currently not applicable, with architectural evidence.
- **Q3:** Astra — its five approved boundaries; remaining findings ratcheted individually.
- **Q4:** Astra — applicable canonical `CODEOWNERS` ownership.
- **Q5:** Astra — its complete risk position.

This is a coherent selection of one voter’s full answer at every split. Selecting Astra’s expanded scope with Opus’s unchanged seam map would omit analysis and health boundaries; selecting that scope with Opus’s scoring-only marker would leave two configured roots uncovered. Broad behavioral scope and nominal-class-rule non-applicability are not contradictory: the reference treats them as separate configuration decisions.