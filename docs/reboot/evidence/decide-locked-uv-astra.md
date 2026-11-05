1. **Resolver pin — HIGH:** Keep `uv@0.10.0`; lock `uv==0.10.0`. All nine manifests, dependency digests, `.deps` locks and registry paths remain unchanged; no re-import.
   Upgrading would require regenerating both architectures’ locks at retained cutoffs, updating manifests/bundles, and creating new dependency-content directories—not new problem versions.
   `38d627ecf668` derives from the source commit, not uv. Ordinary `UPDATE=1` still reuses unchanged dependency sets; upgrading therefore also needs an explicit resolver-refresh path.
2. **Placement — HIGH:** Add uv to `[project].dependencies`, not an extra: grading requires it. PyPI’s `uv==0.10.0` declares no dependencies.
   Preserve every existing locked version; regenerate without `--upgrade` and reject unrelated version changes. Live `_host_runtime()` is Python `3.13.7`, packaging `26.0`, pydantic `2.12.5`; leave it unchanged.
3. **Resolver implementation — HIGH:** Resolve `uv.find_uv_bin()` once per `UvResolver` through a `uv_bin` default factory; both `identity` and `_compile` already consume that field.
   Use its absolute path, with no PATH fallback; translate discovery failure to `DependencyInfraError`. Keep `_PASSED_ENV` unchanged.
4. **Bootstrap — HIGH:** Remove `[tool.uv].required-version`; retain CI’s three `0.10.0` pins solely for reproducible bootstrapping, independent of resolver-pin checks.
   Lock schema is `version=1`, `revision=5`. [uv documents revisions as backward-compatible](https://docs.astral.sh/uv/concepts/resolution/#lockfile-versioning); revision 5 creates no newer-reader minimum. Document compatible bootstrap uv, not a host equality guard.
5. **Guard — HIGH:** Require an unconditional exact uv runtime dependency, its matching locked package/runtime edge, and matching `uv@VERSION` pins across all registered problems.
   Retain missing-Python-pin, byte-equality and running-interpreter checks. Remove host/CI uv equality checks and `uv self update` advice.
   Extend `test_toolchain_pin_check.py`’s acceptance/refusal matrix for missing, malformed and mismatched dependency/lock/resolver pins; delete host/CI mismatch expectations and incidental wording assertions.
6. **Regression coverage — HIGH:** Extend `test_dependency_resolver_slow.py::test_the_same_input_gives_a_byte_identical_lock` with absent/hostile PATH and different working directories.
   Assert packaged-version identity plus identical resolved packages/lock bytes using the default resolver; replace the vacuous `identity.startswith("uv@")` test. Extend existing fast infrastructure-failure coverage for binary discovery failure.
7. **Sequencing — HIGH:** One PR: dependency/code/guard/tests → freeze grading inputs → `make problem-validate PROBLEMS=all TARGET=mbp REPEATS=5` → relock all 12 experiments → README/handoff update → quality, slow/quality tests and CI.
   Use `refactor:`, `chore:`, `test:` and `docs:` without breaking markers; ensure no pending bump-triggering commits. If retaining `fix:`, finish the release bump before final relocking.
   A distribution-only bump leaves grading identity unchanged but stales experiment `toolchain.raidar`; it requires relocking, not another validation.
**Risks:** Live grading digest is `a93cc5f7…`; the resolver edit changes it, invalidating all nine validations. Finish identity-bearing edits before the single revalidation.
**Verification limit:** Read-only runtime checks confirmed digest/closure and repin identity boundaries. uv is not installed in this environment; installation, unchanged-lock proof and PATH/cwd regression execution remain implementation acceptance gates.
