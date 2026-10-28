# Decision: frozen, shared submission dependencies

Citation roots: `R/` = `orchestrator/src/raidar/`; `P/` = `/Users/adamjackson/Projects/inflight/scb-problems/`; `S/` = `/Users/adamjackson/Projects/inflight/slop-code-bench/`.

## 1. Recommendation and verified facts

**[INFERENCE] Adopt a hardened hybrid: resolve references at import, but make the resulting wheel catalog shared across the pinned corpus—not a per-problem reference-library whitelist. Allow pre-locked extensions equally for every arm. Install submission dependencies offline from that frozen catalog. Do not resolve against live PyPI during counted trials.**

[INFERENCE] This fixes the provisioning gap without giving agent-controlled installers network access near hidden tests. A corpus-wide catalog reduces reference-library favoritism and per-problem hints. It remains **constrained SCB grading**, not unrestricted native equivalence: arms choosing packages outside the catalog still face a restriction.

### Findings that change the decision

- **[OBSERVED] The current runtime provisioner is substantially narrower than the brief suggests.** Its only submission-package pin is `PyYAML==6.0.3`, available only when the problem lists PyYAML in `test_dependencies`. Other test dependencies are installed into the verifier’s separate environment, not automatically provided to submissions. Image construction itself uses networked installs. (`R/grading/submission_deps.py:33-85`; `R/grading/grader_image.py:45-55,91-105`)
- **[OBSERVED] The declaration-name scan confirms 36 problems, 23 with nonempty reference requirements, 20 with names absent from `test_dependencies`, and 16 after removing pytest-only gaps.** The scan covered every checkpoint’s root `requirements.txt`, normalized distribution names, and compared names—not resolved transitive closures or actual imports. Representative inputs: `P/datagate/solutions/checkpoint_4/requirements.txt:4-9`, `P/datagate/config.yaml:66-67`, `P/etl_pipeline/solutions/checkpoint_1/requirements.txt:5-8`.
- **[OBSERVED] Against the actual provisioner, 18 problems have non-pytest declaration gaps.** The additional two are `file_merger` and `sheeteval`: their configuration names PyArrow/OpenPyXL, but the runtime pin table does not. Executing the resolver confirmed PyArrow unprovided and only PyYAML provided for `sheeteval`. (`P/file_merger/config.yaml:53-55`; `P/file_merger/solutions/checkpoint_2/requirements.txt:5-6`; `P/sheeteval/config.yaml:65-67`; `P/sheeteval/solutions/checkpoint_1/requirements.txt:4-5`; `R/grading/submission_deps.py:34,62-72,127-160`)
- **[OBSERVED] The supplied checkout’s HEAD is `ef6a9dd…`, not registry pin `38d627ec…`; the tracked configuration and root reference-manifest files scanned showed no differences against that pin.** The importer would reject that checkout’s HEAD as a pinned import source. (`lab/problems/sources.yaml:10`; `R/design/problem/source.py:98-102`)
- **[INFERENCE] “About 20 of 36 experiment-ready” is not established by these counts.** Missing declarations need not affect every checkpoint; name comparison ignores transitive availability. Dependency provisioning also cannot establish that other runtime requirements work.
- **[OBSERVED] Validation permits partly solvable problems, and excludes an individual step whose solvable set is empty.** It does not necessarily exclude the entire problem when later references fail. Three repeats are the default; the solvable-set solve rate is the default primary outcome. (`R/design/problem/validation.py:5-19,230-270`; `R/design/problem/validator.py:54`; `R/scoring/catalog/outcomes.py:9-11,481-485`)
- **[OBSERVED] Commit `fb33b02` removed direct validation’s requirement refusal; current validation calls the same grader used for submissions.** Thus missing dependencies can now affect the measured solvable sets. (`R/design/problem/direct.py:1-10,54-92`; inspected `fb33b02` diff for that file)
- **[OBSERVED] Native SCB runs `uv init`, `uv add -r requirements.txt`, then `uv run`; its default Docker network is bridge.** Raidar instead grades in a separate, non-root, no-network container with `UV_OFFLINE` and `UV_NO_SYNC`. (`S/configs/environments/docker-python3.12-uv.yaml:12-23`; `S/src/slop_code/execution/docker_runtime/models.py:97-107`; `R/grading/sandbox.py:169-183,222-250`)
- **[OBSERVED] Workspace seeding currently declares `test_dependencies` and runs an unfrozen `uv sync`.** Agent egress allows provider endpoints and package registries. Matching dependency names therefore does not ensure matching installed versions or runtime availability. (`R/engine/trial/run.py:64-69`; `R/engine/trial/workspace.py:39-64`; `R/engine/trial/network.py:20-26,69-98`)
- **[OBSERVED] Legacy FCR-007 is now FCR-004 and remains open.** Its recorded failures substantiate the environment mismatch, but the ledger describes the historical observations as symmetric—not proof of an observed between-arm bias. (`lab/ledger/faults/FCR-004.md:2-21,31`)
- **[INFERENCE] Library-dependent arms can nevertheless be disadvantaged systematically.** Equal application of a reference-derived whitelist is not neutrality toward implementation choices.

## 2. Options

[INFERENCE] These are design assessments; coverage and effort are not measured implementation results.

| Option | SCB fidelity | Fairness between arms | Determinism | Security | Need 1 coverage | Effort |
|---|---|---|---|---|---|---|
| (a) Per-problem reference wheels | References supported; alternative libraries restricted | Explicit reference-library advantage | Strong with complete closure, hashes and image pins | Strong offline boundary | Fixes supported reference dependencies; not universal coverage | Medium |
| (b) Live networked submission install | Closest package semantics; offline execution still differs | No reference-library whitelist, but time/index availability can differ | Weak unless resolution universe and artifacts are frozen | Agent-controlled builds execute with network; requires strong separation | Broadest dependency support, still fallible | High |
| (c) Accept current behavior | Known fidelity gap | Rewards avoiding unavailable libraries | Current environment is not fully frozen anyway | Preserves current boundary | Unmeasured restricted subset | Low immediate effort |
| **Recommended: shared frozen catalog + pre-locked extensions** | Native-like declaration resolution inside a published boundary | Same versions and choices for all arms; residual catalog-selection bias | Strong with deterministic offline resolution and retained artifacts | No networked agent installation; wheels remain untrusted runtime code | Automatic onboarding for references supported by the pack/catalog | Medium–high |

## 3. Concrete design

### Generation and ownership

- **[OBSERVED] Workspace setup and grading belong to Raidar, not workflow packages.** Generated import/validation records are already committed beside problem manifests, while materialized content lives under `var/problems/`. (`split-design.md:29-39,120-128,187-218`; `R/design/problem/store.py:1-20`)
- **[INFERENCE] Keep the public sequence:** `make problem-import` → `make problem-validate REPEATS=3` → experiment lock. Import automatically prepares or reuses the catalog for the pinned source and Python pack; no handwritten per-problem dependency files.
- **[INFERENCE] Resolve every reference manifest independently, for every supported platform, and union their wheel artifacts into the shared catalog.** Also resolve the existing workspace seed dependencies. Do not combine all requirements into one environment.
- **[OBSERVED] A single combined environment would already conflict:** ETL pins DuckDB `1.1.1`, while `file_query_tool` pins `1.4.1`; their SQLGlot and pytest pins also differ. (`P/etl_pipeline/solutions/checkpoint_4/requirements.txt:5-8`; `P/file_query_tool/solutions/checkpoint_4/requirements.txt:5-12`)
- **[INFERENCE] Proposed storage:** committed catalog records at `lab/problems/dependencies/<catalog-sha256>.json`; generated per-problem bindings beside the existing manifest as `<version>.dependencies.json`; wheel blobs and materialized environments under `${RAIDAR_VAR:-var}/problems/dependencies/<catalog-sha256>/`. Retain wheel bytes in a durable artifact store: hashes alone cannot reproduce deleted downloads.
- **[INFERENCE] Import records unsupported requirements/platforms explicitly. Validation must refuse dependency-incomplete references before deriving solvable sets.** A missing promised package is a provisioning defect, not evidence that benchmark cases are intrinsically unsolvable.

### Format

[INFERENCE] Use canonical JSON, with immutable content-addressed catalogs and separate host-only reference provenance. Proposed logical fields:

```text
schema: "submission-dependencies/1"
source_commit, pack_release, policy_version
resolver: {tool, version, configuration_sha256}
platforms: {os, arch, python_full_version, abi, marker_environment, image_digest}
catalog: [{name, version, requires_python, requires_dist,
           wheels: [{filename, tags, sha256, size, source_url}]}]
reference_plans: [{manifest_sha256, platform, selected_wheel_hashes}]
seed_plans: [{problem, platform, declarations_sha256, selected_wheel_hashes}]
catalog_sha256, public_inventory_sha256
```

- **[INFERENCE] Resolve complete transitive closures, extras and environment markers.** Preserve multiple versions where different reference plans require them; freeze resolver behavior and candidate ordering.
- **[INFERENCE] Parse declarations statically.** Support explicit, bounded manifest syntax and contained requirements-file includes; reject unsupported dynamic dependencies, VCS/editable/local-path installs, arbitrary index overrides and direct URLs with structured reasons. Never execute a submission’s build backend to discover requirements.
- **[OBSERVED] The current parser is not that contract:** requirements lines beginning with `-` are skipped, and provisioning primarily checks a name and version specifier. (`R/grading/submission_deps.py:88-119,127-160`)

### Agent and grader use

- **[INFERENCE] Give every arm the same public catalog and resolver.** Hide reference-to-package mappings, reference paths and solution contents; expose package availability and exact versions.
- **[INFERENCE] Do not seed the entire per-problem reference union.** It reveals library choices and later-checkpoint hints. Retain D2 seeding from the existing configuration, but generate its lock and install it offline from the shared catalog.
- **[INFERENCE] Availability—not a giant preinstalled environment—must match.** Agent installation commands and grader reconstruction use the same catalog, declaration semantics and interpreter. Agents may select any catalog package, not just packages used by that problem’s references.
- **[INFERENCE] Remove live package-registry access during the measured agent phase; retain required provider access.** Configure supported package tools for the local catalog and provide an immediate dependency-eligibility check. Otherwise an agent can still successfully develop against packages grading cannot reconstruct.
- **[INFERENCE] After snapshot capture, resolve its declarations offline and construct one coherent submission environment.** Rebuild from trusted wheel bytes; never copy the agent’s `.venv`. Keep the verifier’s environment separate, and replace per-package `PYTHONPATH` provisioning rather than layering another policy beside it.
- **[INFERENCE] Preserve no-network grading and non-root execution.** Networked catalog preparation receives only declaration data—no submission execution, hidden tests, credentials, Docker socket or writable host mounts. Wheels can execute code when imported or through startup hooks; they are not inherently safe.
- **[INFERENCE] Extensions are opt-in before counted runs:** pre-lock requested packages and closures, publish them to all arms, and issue a new catalog/experiment lock. Never expand the catalog because one counted trial failed, then rerun only that trial.

### Platforms and identity

- **[OBSERVED] The pack specifies Python 3.12 and a digest-pinned base, but its own documentation says apt/npm transitive contents can vary between builds.** The grader image likewise warns that its Dockerfile-derived tag is not reproducibility proof. (`lab/environments/python/pack.yaml:5-16`; `lab/environments/python/image/Dockerfile:13-17`; `R/grading/grader_image.py:17-21`)
- **[INFERENCE] Produce separate Linux amd64 and arm64 resolution plans using actual pack-compatible CPython 3.12/ABI/platform tags, including compatible `abi3` and pure-Python wheels.** Do not resolve against the host macOS interpreter. Record full Python version, marker environment, shared-library requirements and immutable per-platform image digests.
- **[INFERENCE] Default to wheels only.** Missing compatible wheels block that platform’s dependency readiness; do not silently build an sdist or substitute a version. Both architectures require independent validation, not merely successful downloads.
- **[OBSERVED] Problem identity currently hashes its manifest; experiment execution identity includes resolved problem metadata and solvable sets.** (`R/design/problem/models.py:143-194`; `R/design/problem/resolver.py:80-93`; `R/design/experiment/lock.py:3-17`)
- **[INFERENCE] Add catalog, dependency-policy, resolver, seed-plan and platform/image digests to problem/execution identity.** Validation binds those exact inputs. A dependency change requires revalidation and a new execution lock; old trials must not pool silently with new ones.
- **[INFERENCE] Record each submission’s declaration digest, resolved closure digest and installed-environment evidence as trial outputs.** They are outputs under a frozen policy, not an excuse to alter the experiment lock after observing an arm’s choices.

### Health reading

- **[OBSERVED] Verifier records already carry `declared`, `provided` and `unprovided`; the health audit does not currently aggregate that field.** (`R/grading/submission_deps.py:54-59`; `R/grading/records.py:76-93,217-218`; `R/engine/health/audit.py:271-291`)
- **[INFERENCE] Per step, record declaration provenance, marker-skipped requirements, selected versions/hashes, and structured unprovided reasons:** absent catalog entry, incompatible version/platform, unresolved conflict, unsupported declaration, or artifact/integrity failure.
- **[INFERENCE] Per trial, report whether any graded step had unprovided declarations and which packages/checkpoints were affected. Per arm, report affected-trial and affected-step numerators/denominators, stratified by problem, checkpoint and platform.** Deduplicate repeated declarations; report ungraded/missing evidence separately, never as zero.
- **[INFERENCE] An out-of-policy agent declaration remains a valid agent outcome, not an exclusion or replacement trial.** Failure to provide a package promised by the frozen catalog is an instrument failure. Do not attribute every failing case to an unprovided declaration without additional evidence.

## 4. Risks and tests

- **[INFERENCE] Residual fairness risk:** a finite catalog still favors some library choices. Test alternative implementations using non-reference catalog libraries, and publish catalog-boundary failures beside outcomes; health reporting does not remove bias.
- **[INFERENCE] Hint risk:** test that agent-visible files contain no reference mappings or future-checkpoint provenance. The shared inventory still reveals corpus-level package availability; disclose that intervention.
- **[INFERENCE] Resolution tests:** conflicting versions, extras, transitive dependencies, markers, prereleases, malformed manifests, bounded includes, forbidden URLs/builds, and declaration changes across checkpoints.
- **[INFERENCE] Environment tests:** identical declared closure in work and grading environments; console scripts, native extensions, package data and metadata; no verifier-package leakage; no dependence on discarded `.venv` contents.
- **[INFERENCE] Security tests:** malicious wheels/startup hooks, archive traversal, poisoned caches, manifest-controlled indexes and build backends; verify no execution during networked acquisition and no network/credentials/host access during submission execution.
- **[INFERENCE] Reproducibility tests:** cold/warm reconstruction on both architectures, offline artifact retrieval, hash corruption, vanished upstream distributions, stale-lock rejection and historical regrade.
- **[INFERENCE] Acceptance:** provision every reference successfully before three-repeat validation; compare per-case results with native SCB using the same resolved versions, and separately characterize divergence from native’s live resolution. Do not call dependency installation alone “Need 1 complete.”
- **[OBSERVED] Existing pack limits extend beyond Python packages:** its Dockerfile explicitly omits Docker CLI/Compose and identifies later `recli` checkpoints as requiring a derived image. (`lab/environments/python/image/Dockerfile:7-12`)
- **[OBSERVED] Verification performed here:** public-command discovery, read-only declaration scans, direct execution of the existing dependency resolver, and inspection of the phase-3 diff. No Docker, trials or repository edits; wheel availability and grading outcomes were not exercised. (`Makefile:151-160`; `R/grading/submission_deps.py:62-72,127-160`; `var/tmp/c1-deps/brief.md:5`)

## 5. Open questions for the owner

1. **[INFERENCE] Is “SCB under a published frozen dependency policy” acceptable as the reported benchmark?** If unrestricted native package choice is mandatory, choose a frozen-index resolver service instead; a finite reference-derived catalog cannot substantiate that claim.
2. **[INFERENCE] Must every supported problem work on both architectures, or may readiness be platform-specific?** Default recommendation: label support explicitly and never silently substitute architecture.
3. **[INFERENCE] Are sdist-only dependencies out of scope?** Default recommendation: yes; exceptions require isolated builds before experiment locking, retained wheel artifacts and a new policy identity.
4. **[INFERENCE] Who owns artifact retention and catalog-extension approval?** Those responsibilities determine whether old experiment locks remain executable and whether new package choices are admitted consistently across arms.
