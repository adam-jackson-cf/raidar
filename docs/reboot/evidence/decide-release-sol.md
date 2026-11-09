# Tie-break: release automation — GPT-6.1 Sol

## R1 — Q1: release preparation

**Chosen: Opus**

**Reason:** Adopt Opus’s complete on-demand release-PR proposal, including `make version-release` and gate-enforced `version-check`. Protected-branch compliance requires reviewed pre-merge writes, not a release in every PR.

Checked facts support separating releases from ordinary changes:
- `CHANGELOG.md:5–35` contains repeated `1.0.1` headings for non-release changes.
- `bump-version.py:129–132` already recognises both proposed release boundaries.
- `bump-version.py:202–217` already stages and checks the accompanying dependency-lock update.

Per-PR preparation would make ordinary concurrent PRs contend over shared release metadata; retaining the version field would additionally require relocks for every bump. Opus’s paired R1/R2 decisions avoid both couplings.

The proposed `version-check` is new functionality: existing `--dry-run` only prints and returns success (`bump-version.py:307–314`), so merely invoking it cannot enforce the chosen comparison.

## R2 — Q2: lock toolchain version

**Chosen: Opus**

**Reason:** Adopt Opus’s complete removal-and-relock cutover, including unchanged execution hashes and no `LOCK_VERSION` bump.

Checked facts:
- `lock.py:163–166` explicitly identifies `raidar_spec`, rather than the installed release version, as tying locks to exact validation, metric and lock rules.
- `lock.py:188–192` records toolchain versions outside job identity.
- `lock.py:567–574` derives `lock_sha256` exclusively from the execution section.
- `engine/job/provenance.py:53–61` records checkout commit, dirty state, spec digest and execution-code digest.

Thus removing this coarse supplementary field does not require inventing replacement provenance. Regenerate every tracked lock through `make experiment-lock`, compare execution hashes before/after, and reject unexpected identity changes. Toolchain/document hashes may change legitimately.

## Q3 — workflow deletion: agreement confirmed

**Chosen: Opus**

**Reason:** Both voters agree on deletion; there is no substantive split here. Opus’s complete answer supplies the implementation details consistent with R1.

The workflow directly commits `[skip ci]` and pushes (`version-changelog.yml:66–72`), including when manually dispatched. It also runs unpinned network formatting (`:49–55`). Delete the workflow, retain and revise the useful scripts behind make targets, and move version-wiring checks into required quality checks.

## R3 — Q4: operating-risk policy

**Chosen: Opus**

**Reason:** This is a further substantive split: Astra accepts mandatory per-PR preparation and its prerequisites; Opus accepts delayed releases and removes release-driven relocking. Choose Opus’s complete risk answer, consistent with R1/R2—not Astra’s per-PR controls grafted onto it.

The checked provenance fields above support accepting a lagging human-readable release version without losing recorded execution provenance. Release-PR rebase drift and unchanged execution hashes remain explicit acceptance concerns. The existing 100-commit scan and wall-clock date (`bump-version.py:113–135,220–226`) mean release-check correctness must be demonstrated, not assumed from the current generator.

## Adopted decision

- **Q1:** Opus — on-demand release PR through `make version-release`; `make version-check` joins required quality checks.
- **Q2:** Opus — remove `toolchain.raidar`; regenerate all tracked locks through the public target, requiring unchanged `lock_sha256`.
- **Q3:** Both agree; adopt Opus’s complete deletion and script-migration answer.
- **Q4:** Opus — accept on-demand release lag; enforce release-entry correctness and identity-preserving cutover safeguards.

Read-only review; no files changed or checks executed.