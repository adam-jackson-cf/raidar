# Vote: release automation — GPT-6 Astra

## Q1

**Decision:** Move version and CHANGELOG generation into each PR, before its final verification, through a root `make release-prepare` target. No post-merge repository writes.

The target should:
- Derive the highest required bump from the PR’s Conventional Commits against an explicit, current `main` base.
- Generate from that base’s version and CHANGELOG, not from already-generated branch output. Repeated execution must be idempotent.
- Update `orchestrator/pyproject.toml` and `orchestrator/uv.lock` together. Non-bumping changes update the existing version’s notes without creating duplicate version headings.
- Use a stable date input and pinned formatting dependencies.
- Refresh installed package metadata, then regenerate every tracked experiment lock when the Raidar version changes.

`gate` must check the expected version, CHANGELOG and dependency-lock state; existing CLI version wiring; and full quality checks, including experiment-lock freshness. Its release check must compare expected output and fail on differences—not merely invoke the existing informational `--dry-run`.

Require the PR to be current with `main` before final exact-head `gate` and cross-family verification. If `main` advances, rebase, regenerate and verify again before rebase-merge.

**Reason:** This makes release metadata, locks and implementation one reviewed, gated transaction, without PAT/App credentials or bypass permissions. The current generator’s last-marker/100-commit scan and wall-clock date are unsuitable for deterministic PR validation (`bump-version.py:113–135,220–226`). `verify-version-wiring.py` checks CLI sourcing, not release correctness.

## Q2

**Decision:** Keep recording and checking `toolchain.raidar`. Move all necessary relocking into the same PR as the bump.

After preparing the version and synchronising the project environment, run `make experiment-lock EXPERIMENT=<path>` for every tracked experiment. Commit those generated locks before final verification; never hand-edit their contents or repair them in a later PR.

For a metadata-only bump, require unchanged execution `lock_sha256` values. If execution identity changes, stop treating the change as routine release bookkeeping and apply the relevant release/revalidation process. Semantic toolchain changes still require relocking even when the package version does not change.

**Reason:** The immediate defect is transaction ordering, not an inherently invalid provenance field. Keeping the version avoids expanding this repair into a lock-format and provenance redesign.

The version is supplementary provenance, not sufficient reproducibility by itself: retain `raidar_spec`, dependency pins and existing content identities. `lock.py:188–192` records the version outside execution identity, and `seal_lock()` derives `lock_sha256` solely from execution (`567–574`). Therefore, a version-only relock can change toolchain/document hashes without invalidating existing jobs. Atomic pre-merge preparation prevents the stale-`main` interval documented in the handoff.

## Q3

**Decision:** Delete `.github/workflows/version-changelog.yml`. Reuse and revise its useful generator/checker logic behind the make targets and required `gate`; remove obsolete post-merge commit-marker behaviour.

**Reason:** Neither its push trigger nor `workflow_dispatch` makes its direct push admissible. Retaining it would preserve a broken alternative release path. Remove its write permission, `[skip ci]` commit generation and unpinned `npx` formatting rather than introducing privileged automation.

## Q4

**Decision:** Accept the additional pre-merge preparation cost, subject to these controls:

- **Concurrent PRs:** require current-base preparation and fresh exact-head verification; otherwise independently valid PRs can allocate the same next version.
- **Generation loops:** release bookkeeping must not become new changelog input or cause another bump. Identify generated bookkeeping narrowly; do not let arbitrary release-like commit subjects hide substantive changes.
- **Installed metadata:** relocking against an old editable installation can record the wrong version. Synchronise before generation and check against project metadata.
- **Relock scope:** regenerate all affected locks through the public target; never suppress freshness checks or rewrite hashes manually.
- **Unavailable release prerequisites:** missing validations or required local inputs block preparation, not justify merging stale locks.
- **Commit classification:** Conventional Commits can misstate semantic impact. The checker enforces policy; review must assess whether the declared bump is appropriate.

**Reason:** These risks are visible before merge and fail closed. They are preferable to protected-branch write failures, credential-heavy bot infrastructure or a knowingly stale default branch.