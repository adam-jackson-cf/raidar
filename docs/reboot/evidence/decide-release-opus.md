# Vote: release automation — Claude Opus 5.5

## Q1

**Decision:** Stop writing the version and CHANGELOG on every merge. Releases become an on-demand **release PR** that goes through the same route as any other change. Do not bump in every PR, do not add a bot or PAT, and do not use a tag-driven release.

- **Make target:** `make version-release` wraps `scripts/release/bump-version.py`. It refuses to run unless HEAD equals `origin/main`. It also refuses when the commits since the last release are all no-bump types, so CHANGELOG stops getting a fresh duplicate `## 1.0.1` heading for every `docs`/`ci`/`refactor` merge.
- **What it writes:** `orchestrator/pyproject.toml`, `orchestrator/uv.lock` (through the existing staged `uv lock` + `--check`) and one CHANGELOG entry. That entry covers every conventional commit since the last `chore(release):` or `chore: bump version` commit; the script already treats both as boundaries.
- **Commit and merge:** the writer commits the result as `chore(release): X.Y.Z` on a branch. It then takes the normal exact-head `gate`, cross-family verification and rebase-merge.
- **What `gate` checks:** a new `make version-check` step in `make quality`:
  1. `verify-version-wiring.py`, which today runs only in the workflow being removed.
  2. `[project].version` in pyproject equals the version recorded for the editable `raidar` package in `uv.lock`.
  3. The top CHANGELOG `## X.Y.Z` heading equals the pyproject version.
  4. Only when HEAD's subject is `chore(release):`: the top CHANGELOG entry equals what `bump-version.py --dry-run` computes from HEAD's parent. Because `gate` runs on the exact head, a release PR that is rebased after `main` moved fails here and has to be regenerated. This catches the case where a rebase-merge pushes commits below the release boundary and drops them from the CHANGELOG.

**Reason:**
- **Bumping in every PR fails under concurrency.** Agents rebase-merge PRs that run at the same time. Each one would conflict on the CHANGELOG head and the version line, and every rebase would invalidate its exact-head `gate` and verification.
- **A bot PR adds a secret and a second path.** It needs a long-lived PAT or App credential, and it bypasses the verification route agents already use.
- **Release-on-tag still has to write the static version.** It must commit the pyproject version somewhere, so it needs one of the two options above anyway.
- **The per-merge record already exists.** Raidar is not published, and job identity does not use the version (Q2), so a release cadence chosen by a person costs nothing. The Conventional Commit log on `main` is already the per-merge record. The current CHANGELOG shows the automation adds noise: four `## 1.0.1` sections in a row (2026-11-04 to 2026-11-09).

## Q2

**Decision:** No. Drop `raidar` from `TOOLCHAIN_PACKAGES` in `orchestrator/src/raidar/design/experiment/lock.py`, leaving `("litellm",)`. Keep `python`, `litellm` and `raidar_spec` in `toolchain`.

**What already covers reproducibility (no new field needed):**
- `toolchain.raidar_spec`, the AST digest of the spec, metric and lock rules. Its own docstring says the installed `raidar` version "is static between releases; this digest is what ties a lock to the exact … rules".
- Each job's `runner` provenance, from `engine/job/provenance.py`: `commit`, `dirty`, `toolchain_sha256` and `execution_sha256`.
- The version is a strictly coarser proxy. `1.0.1` spans many code-changing commits in `git log`.

**Cutover, in one reviewed PR** with a subject that does not bump (`refactor(lock): …`, no `!`):
1. Change `TOOLCHAIN_PACKAGES`.
2. Reword `STALE_CONSEQUENCES["toolchain"]` and the module docstring line `toolchain: Python, Raidar, LiteLLM …`.
3. Update the tests that assert toolchain keys.
4. Relock all 12 `lab/experiments/**/experiment.lock.json` files, only through `make experiment-lock EXPERIMENT=<path>`.
5. Record before and after `lock_sha256` for each lock in the PR. They must be identical, because `toolchain` is outside the `execution` digest, so jobs and baselines still match.
6. `make experiment-locks-check` must pass.
7. Remove the handoff Open item "Version-bumping merges leave the experiment locks stale."

Leave `LOCK_VERSION` unchanged. Its documented bumps track identity and shape changes, and the existing section comparison already reports the change as `toolchain` stale.

**Reason:**
- Lock code says `toolchain` is "recorded (N3) but … not part of any job's identity" and relocking it means "jobs still match". So the field buys no reproducibility, yet it couples every release to a 12-lock relock.
- Relocking needs the problems registered under `var/`, and a release PR should not depend on that.

## Q3

**Decision:** Delete `.github/workflows/version-changelog.yml`. Keep `scripts/release/bump-version.py` as the engine behind `make version-release`, with these changes:
- Remove the `[skip ci]` subject. Release commits come from a PR and must get `gate`.
- Make release commits use `chore(release): X.Y.Z`. Keep `chore: bump version` as a recognised boundary so history still parses.
- Add the no-bump refusal.

Move `verify-version-wiring.py` into `make version-check`. Drop the unpinned `npx --yes prettier` step:
- Either make the generator's output stable on its own,
- or, if formatting must be checked, use a pinned formatter through the repo's existing toolchain.

Update `test_release_bump_version.py`, `test_execution_boundary_rules.py` and `make help`.

**Reason:**
- Rewriting the workflow does not help: whether it runs on push or on `workflow_dispatch`, it ends in `git push` to `main`, which the ruleset rejects (no bypass is possible on this user-owned repo).
- Opening a PR from the workflow with `GITHUB_TOKEN` does not trigger `gate`.
- Keeping a dead workflow invites someone to "fix" it with a PAT.
- An unpinned network formatter conflicts with the repo's locked-toolchain rule.

## Q4

**Risks:**
1. **Rebase-merge drops commits from the CHANGELOG.** If `main` moves after the release branch is cut, rebasing puts the newer commits below the boundary and they disappear from the entry. The exact-head CHANGELOG check in `version-check` catches this, and the fix is to regenerate. [INFERENCE] I did not check whether the ruleset requires branches to be up to date.
2. **Releases may lag or never happen,** since they are on-demand. Impact is low: nothing reads the version for identity. The CLI `--version` may stay stale; accept that.
3. **Locks lose the human-readable version.** Jobs keep `commit` and digests, and readers must map a commit to a release through the CHANGELOG. [INFERENCE] Some test or analysis code might read `toolchain.raidar`. A grep of `orchestrator/src` found no reader except lock.py; I did not search tests in full.
4. **A cutover mistake could change identity.** A relock that unexpectedly changes `lock_sha256` would split experiments. Mitigation: compare `lock_sha256` before and after, and refuse the PR on any difference.
5. **The bad commit stays on `main`.** The CHANGELOG-only commit `66c52e8e` (`[skip ci]`, no `gate`) is already there. Leave it; nothing hand-edits history.
6. **`litellm` still records a version.** It only changes with dependency PRs, which already relock deliberately, so the same coupling does not come back.
7. **Release commits change files `gate` covers.** Every `chore(release):` PR edits `uv.lock`. `gate`'s `uv sync --frozen` and `toolchain-pin-check` must still pass on it.