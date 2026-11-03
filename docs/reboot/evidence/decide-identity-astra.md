Decision: a (high confidence) — Slice C contains real error-handling fixes, not only behaviour-preserving refactors; reverting would restore defects.
Files: restricted `git diff 0b46b2eb..HEAD --stat` reports four files, 89 insertions(+), 34 deletions(-), all under `orchestrator/src/raidar/grading/`.
`grader.py`, `verifier.py`: [INFERENCE] decision extractions preserve grading behaviour; `sandbox.py`: injected subprocess seam preserves default execution.
`resolver.py:374–378`: empty/single-token `uv --version` output now raises `DependencyInfraError`, rather than leaking `IndexError`.
`resolver.py:536–552`: malformed TOML/missing package keys now raise `DependencyInfraError`, rather than leaking parser/key exceptions; validation ordering also changes.
These fixes matter: `grader.py:153–156` catches that infrastructure exception and records `dependency_resolver_unavailable`; reverting loses this handling.
Keep C; revalidate all nine problems with five repeats on local OrbStack, then regenerate experiment locks. Retain RT7 and trial-plan identity checks.
Future rule: assess identity impact before test-driven source edits; prefer existing seams when changing tests alone.
Changes affecting grading digest require revalidation; `primitive_code` changes require affected locks regenerated; released package bytes remain immutable—publish a new release.
Never bypass identity gates or rewrite validation pins to avoid validation, even for behaviour-preserving refactors.
Verification: source, commit diff and regression cases inspected only; no tests or validation run, no files edited.
