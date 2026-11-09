# Vote: Full guardrails — GPT-6 Astra

## Q1 — Identity-bearing bytes

**Decision:** Adopt narrowly scoped, evidence-backed baseline exceptions with a shrinking ratchet:
- **Released packages:** preserve shipped bytes and existing `@N` identities. Repair baseline findings when an independently justified package change requires its next release.
- **Grading closure:** defer AST-changing repairs until the next deliberate grading-identity change. Identity-neutral formatting may proceed only with demonstrated digest equality.
- **Primitive modules:** likewise defer identity-changing repairs until an independently justified primitive change.
- **`contract/src`:** defer remediation to its next deliberate contract release; never overwrite the released 2.0.0 artifacts. Apply grading/primitive protections wherever its modules participate in those closures.

**Reason:** `split-design.md` §5.4 and the handoff distinguish byte-hashed packages from AST-hashed execution identities; neither annotations nor docstrings are automatically harmless. A cosmetic compliance batch is insufficient justification for new releases, invalidated validations and approximately six hours of revalidation. Exceptions should name exact files/findings, affected digests, owners and retirement triggers—not exclude whole directories from checking. New violations remain forbidden. At the next relevant identity change, remove applicable exceptions, review before/after identities in a dedicated commit, perform required release/security checks, schedule revalidation and regenerate locks through public commands. No test-only unit may change these identities.

## Q2 — Catalogue deviations

**Decision:** Keep **line length 100** as an explicit deviation; retain **Ruff `py312` and mypy `3.12`** as the supported-language targets; keep execution pinned to **Python 3.13.7**. Adopt **D with NumPy convention** and **`preview = true`**, using the pinned Ruff version and the agreed ratchets.

**Reason:** Moving from 100 to 88 buys no demonstrated correctness improvement and creates unnecessary formatting and package-identity churn. The catalogue actually makes Ruff/mypy language targets target-derived; the draft’s mypy 3.13 is not an obligation to abandon declared Python 3.12 compatibility. Runtime identity remains a separate concern. NumPy is the catalogue’s explicit docstring convention, so adopting it avoids another unnecessary deviation, while grandfathering identified findings prevents gratuitous identity changes. Pinned preview behavior is reproducible and exposes requested rules such as positional-argument checks; tool upgrades must receive deliberate configuration, baseline and canary review rather than silently changing enforcement.

## Q3 — Non-identity backlog

**Decision:** Enable the complete lint/type configurations through a **per-file, finding-specific baseline that can only shrink**, rather than repairing the entire backlog first. Require clean new files and forbid new suppressions or baseline expansion without a separately approved policy change.

**Reason:** Roughly 956 Ruff findings and 1,950 mypy errors make a prerequisite cleanup a large, risky cutover that delays useful protection. A ratchet immediately stops deterioration while allowing focused repairs. It must compare identifiable findings—not merely per-file counts, which permit replacing one violation with another—and reject unknown files, missing scans and unreviewed exclusion changes. Fixed findings must leave the baseline; additions, renames and diagnostic drift must not silently refresh it. This is an explicit adoption deviation with retained debt, not evidence that existing code is fully strict-clean. Missing checkers and missing rule evidence cannot be grandfathered as lint debt.

## Q4 — Commit hook scope

**Decision:** Use one public commit target that runs **full `make quality` plus affected T2 seams** and applicable Full checks. Run full quality and **all T2 seams on every push/PR** through `checks.ci`. Do not initially introduce narrower static checking.

**Reason:** Bare `make quality` is insufficient for the catalogue’s affected-T2 obligation. Conversely, affected tests alone cannot replace every T1 test. The measured fast suite already dominates runtime, so narrowing static checks offers limited demonstrated benefit while introducing dependency-selection and coverage risks. Reuse the authoritative quality implementation, add conservative seam selection, and run all relevant seams when ownership cannot be determined safely. Accept the measured two-to-three-minute quality cost honestly; the completed commit gate will cost more. Keep hooks check-only, with exact command dispatch and failure propagation—no automatic fixing, staging or dependency installation.

## Q5 — Ordered PR units

**Decision:**

1. **Policy and identity inventory:** record the approved deviations, exact protected closures, baseline ownership and all 104 rule obligations. Preserve unresolved obligations as blocked.
2. **Bootstrap gate:** land schema-2 `gate.json`, the existing hook-manager cutover, dispatcher, path adapter and public commit/CI targets. Schedule `gate` on every push/PR, running current quality and the required test tiers. Install/prove the hook; require the observed `gate` status on the host, preserve existing protections and keep agent merge allowed. Explicitly label this bootstrap—not completed Full adoption.
3. **Tooling and ratchet cutover:** integrate pinned tools, Full Ruff/mypy settings, precise approved baselines, suppression/inventory checks and dependency parity/audit controls. Begin non-identity repairs under the shrinking baseline.
4. **Complete missing enforcement:** implement ownership/PYQ/PYS, test-growth and duplicate-path, seam, timeout and metadata checks with positive/negative canaries. Integrate budget enforcement only after at least five recorded runs and explicit approval of target-owned limits.
5. **Full evidence cutover:** complete and review all 104 rows, prove local/CI invocation and host/hook behavior, then make the complete rule-evidence validator mandatory and fail-closed in both command paths. Missing implementations, stale evidence and unapproved exceptions must fail.

Use truthful non-version-bumping tooling/test/refactor commit categories for these units. Keep identity-changing releases outside this adoption sequence. Any unavoidable version-bumping merge requires its immediate follow-up relock PR before subsequent work proceeds.

**Reason:** Early bootstrap establishes real protection without pretending that the draft’s 104 blocked evidence rows pass. Ratchets then make stronger configurations usable without breaking existing quality or forcing identity churn; bespoke implementations and measured budgets close the remaining semantic gaps before the final evidence switch. Every PR must retain green `make quality` and existing coverage floors. The release script explicitly exempts chore/test/refactor changes from bumps, so honest classification avoids needless lock invalidation without changing release policy. The bootstrap must also reconcile existing release automation’s `[skip ci]` commits with the every-push gate requirement. Host settings need live readback, not merely a desired-settings file.

## Risks

- Baselines can become permanent exemptions; require named ownership, visible debt and enforced retirement triggers.
- Full with approved baseline/configuration deviations is not pristine catalogue compliance; report that distinction explicitly.
- Proposed 30-second test budgets are already missed. Neither five-run measurement nor Full selection authorizes silently raising them.
- Protected `docs/` scope needs explicit permission before scanning or formatting; lack of permission is a blocker, not evidence of compliance.
- Required checks must preserve legitimate release automation without creating a bypass or skipped-check deadlock.
- No checks were executed for this vote; measurements above come from the supplied brief and proposal.