Decision: c (high confidence) — dependency-based grading identity, but batch its cutover with 1.1.0 and the distribution split; revalidate once afterward.
Current scope: `grading/**/*.py`, local `raidar/contract/**/*.py`, every installed `raidar_contract/**/*.py`, three raw payload files, and Python/packaging/pydantic versions (`records.py:115–147`).
Validation pins that digest; RT7 and `engine/trial/plan.py:177–196` reject mismatches. Nine problems/~6 hours is supplied operational evidence, not independently timed.
Host modules: actual verifier/offline imports and AST closures both yield only `raidar_contract` (`__init__.py`) and `raidar_contract.canonical`; no contract Pydantic models.
Sandbox payload: no contract imports; `raidar_oracle_plugin.py` dynamically imports `coverage`, and `sitecustomize.py` adds dependency paths.
Critical catch: literal (b) still hashes package initializers; `__init__.py` contains `__version__ = "1.0.0"`. A version-only edit changes its normalized AST, confirmed in memory.
Rule: hash complete grading-entry import closures, including initializers and nested imports; explicitly include subprocess/plugin roots, shipped payload/configuration, and behavior-relevant dependency identities.
Separate distribution-version metadata from executable initialization in the planned release; never exclude whole initializers or silently relax AST normalization.
Risks: static closure misses name-loaded modules/models, plugins and data reads. No name-loaded contract models were found; dynamic `coverage` loading is present.
Guards: explicit dynamic-root/resource inventory, refusal of unaccounted dependencies, representative host/sandbox import tracing, and behavioral mutation checks; traces alone cannot prove completeness.
Sequencing: review the identity rule separately now; land 1.1.0, then the split, prove the final inventory, revalidate and relock before resuming trials. Intermediate trials require intermediate revalidation.
`.agents/skills/test-patterns/SKILL.md`: this is not test-only work; require its own reviewed commit, before/after identities, rationale and scheduled revalidation; preserve frozen releases and `primitive_code` identities.
Verification: read-only digest execution, actual entry-point imports and closure inspection; no edits or grading runs.
