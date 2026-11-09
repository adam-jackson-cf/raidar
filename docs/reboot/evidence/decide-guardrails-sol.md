# Tie-break: Full guardrails — GPT-6.1 Sol

## T1 — D401 and formatter preview

Chosen: Opus

Reason: Adopt Opus’s complete answer: NumPy convention with an explicit D401 deviation, lint preview enabled, and formatter preview disabled. The recorded Full scan attributes 564 findings to D401 alone (`local://guardrail-proposal.md:382`), while the catalogue confirms NumPy convention and preview as defaults rather than demonstrating a repository-specific benefit from imperative summaries or preview formatting. Frozen packages are byte-hashed (`split-design.md:416–418`), so stable formatter behavior also limits avoidable upgrade churn. These are explicit deviations requiring approval—not claims of unmodified catalogue compliance.

## T2 — Non-identity remediation

Chosen: Astra

Reason: Adopt Astra’s complete finding-specific, shrink-only baseline strategy rather than Opus’s mechanical-cleanup prerequisite and per-(file, code) counts. The measured backlog is substantial—1,193 Ruff findings and 1,985 mypy errors in the recorded scope (`local://guardrail-proposal.md:382–383`). More importantly, equal counts cannot distinguish retained debt from a fixed violation replaced by a new one; Opus explicitly acknowledges that weakness (`local://decide-guardrails-opus.md:92`). Astra’s identifiable findings, mandatory removal of fixed entries, suppression prohibition, and rejection of incomplete scans better enforce “no new violations” without making bulk cleanup a prerequisite.

## T3 — PR-unit order

Chosen: Opus

Reason: Adopt Opus’s seven-unit order: bootstrap gate, identity guard, Ruff, strict mypy, missing checkers, measured budgets, then fail-closed evidence. The existing quality chain already includes release and experiment-lock checks (`local://guardrail-proposal.md:34,46–47`), making it a useful bootstrap rather than an empty gate. The separate executable identity guard before tooling changes is particularly valuable: grading identity combines normalized source hashes with raw-byte runtime hashes (`orchestrator/src/raidar/grading/identity.py:167–178`). The final evidence flip belongs last because all 104 draft adoption-proof rows remain blocked (`local://guardrail-proposal.md:58`). T2 determines baseline semantics; this choice adopts Opus’s sequencing, not its rejected count-based baseline.

## T4 — Q1 identity exceptions and digest-neutral repairs

Chosen: Opus

Reason: The votes agree against lint-only releases or identity-changing cleanup batches, but substantively differ on directory exclusions versus finding-level exceptions and on proactively repairing identity-neutral docstrings. Choose Opus’s complete identity policy. Its crucial factual correction is verified: `normalised_source()` calls `_strip_string_statements()`, which removes string-only statements before hashing (`orchestrator/src/raidar/contract/source_digest.py:34–51,82–95`). Ordinary docstring edits therefore do not inherently change `python-ast/1`, contrary to the brief and Astra’s cautionary explanation. Opus also distinguishes raw-byte runtime payloads and explicitly includes lock/scorer identities; the source-digest module confirms those additional consumers (`:12–14`). Digest-bound frozen-path exclusions and demonstrated before/after equality preserve identity while permitting genuinely neutral repairs.

## Q4 — Agreement confirmed

Both votes choose full `make quality` plus affected T2 seams at commit, with full required tiers on every push/PR; neither adopts narrower initial static checking. Their additional hook details are complementary, not conflicting. This matches catalogue `GATE.pre-commit` scope: static checks, all T1 tests, and affected T2 seams. No further tie-break is needed.

## Adopted decision

- **Q1 — Opus:** Preserve released package and raw-payload bytes through digest-bound exclusions. Repair digest-neutral docstrings/formatting with equality evidence; defer identity-changing repairs to independently justified identity changes. Include grading, primitives, lock/scorer closures, and the next contract release.
- **Q2 — Shared settings plus Opus T1:** Keep line length 100 as an approved deviation; target Ruff/mypy at Python 3.12. Adopt NumPy D with an approved D401 exception; enable lint preview and retain stable formatter style.
- **Q3 — Astra:** Enable complete lint/type configurations with finding-specific, per-file shrink-only baselines; prohibit new violations, suppressions, and silent baseline refreshes.
- **Q4 — Agreement:** Commit runs full quality plus affected T2 seams and applicable Full checks; CI runs full required tiers through the always-scheduled `gate`.
- **Q5 — Opus order:** Bootstrap → identity guard → Ruff → strict mypy → missing checkers → measured/approved budgets → fail-closed evidence. Use T2’s baseline semantics throughout; retain green quality, coverage floors, and unchanged identities.