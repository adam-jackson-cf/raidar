Decision: a (high confidence) — add `orchestrator/.python-version` containing `3.13.7`; retain the identical root pin for root-level tools.
Guard: add a first-step `make quality` prerequisite checking both pins agree and the project interpreter’s `platform.python_version()` matches; CI already invokes this target.
Reason: uv documents that pin discovery stops at project boundaries; the orchestrator-local pin covers direct uv commands and every Makefile invocation.
Verification: uv 0.13.7 reproduced root → 3.13.7 and `--project orchestrator` → 3.14.10 in both checkouts; explicit positional request `3.13.7` selected correctly.
Identity: [INFERENCE] pin files and Makefile checks leave grading identity unchanged with identical runtime dependencies; they are outside `grading_execution_inventory()` inputs.
Compatibility: leave both `requires-python` ranges and `uv.lock` unchanged; do not restrict the portable `raidar-contract` package.
Runtime guard: useful fail-fast protection, but adding it inside the grading import closure changes identity and requires revalidation/relocking; omit from this machine-readiness change.
Handoff text: “Run `env -u AGENT uv sync --project orchestrator --python 3.13.7 --all-extras --frozen`, then `env -u AGENT make quality`; retain both matching pins.”
Limit: no files edited; no sync, make, or digest execution performed. Post-change interpreter selection and digest preservation remain acceptance checks.
