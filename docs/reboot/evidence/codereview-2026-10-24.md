# Repository deep review — 2026-10-24

The original findings below are retained for audit. See [Remediation status](#remediation-status) for the changes implemented on 2026-10-25 and the remaining permission/verification limits.

## Scope and method

Twenty subagents reviewed the working tree rooted at this repository, starting from commit `d02608f`. The user explicitly authorized access to `docs/`. Reviewers traced production call paths, checked current documentation against implementation, ran existing focused tests, and used isolated synthetic probes to test suspected failures. Findings describe the reviewed working tree, including pre-existing edits; they are not an assessment of a pristine commit. Other work continued changing the checkout during review; final HEAD inspection showed `124d5af`, so the report is not an atomic snapshot of one revision.

Only this report was authored by the review. No fixes, releases, relocks, commits, real-model trials, target pruning, or Docker/SSH workloads were initiated. Generated `var/` artifacts and treatment payloads under experiment `components/` were excluded from general review; temporary synthetic fixtures were used for validation. Historical evidence and explicitly proposed designs were not classified as defects merely because they predate the current architecture.

CodeGraph 0.9.9 was available but the repository index was uninitialized. Review used direct source tracing rather than changing repository indexing/configuration. Findings below are deduplicated by failure mechanism. A successful focused test suite is supporting baseline evidence, not proof that the separately reproduced defect is covered.

Severity: **High** means a material isolation, evidence-integrity, or verdict failure; **Medium** means a supported workflow or measurement can fail or mislead under a concrete condition; **Low** means bounded diagnostics, instructions, or recovery failures. Proposed fixes are recommendations only.

## Findings index

The detailed entries are the authoritative findings. `B` entries describe implementation defects; `D` entries describe documentation or contract inconsistencies, including cases requiring a policy decision rather than assuming the implementation is wrong.

**54 distinct findings: 11 High, 32 Medium, 11 Low.** Of these, 41 are implementation defects and 13 are documentation/contract findings. Some implementation findings also contradict documentation; those contradictions are included in the same entry, not counted twice.

| ID | Severity | Finding |
|---|---|---|
| B01 | High | Candidate `ruff.py` executes on the assessor host |
| B02 | High | Component symlinks bypass hidden-test exclusion |
| B03 | High | Declared agent network allowlist is never applied |
| B04 | High | Existing remote Docker contexts are implicitly trusted |
| B05 | High | Snapshot serialization permits trivial digest collisions |
| B06 | High | Failed revalidation can leave a problem admitted |
| B07 | High | Quality selects Assessments from the wrong scoring configuration |
| B08 | High | Review aggregation bypasses provenance and tamper gates |
| B09 | High | Aggregate verdict can reverse its own effect and confidence interval |
| B10 | High | Leak guard permits agent execution after detecting hidden tests |
| B11 | High | Offline coverage cache reuses another grading run's parity |
| B12 | Medium | Execution changes retain old smoke and pooling eligibility |
| B13 | Medium | Multi-agent smoke workflow cannot bootstrap |
| B14 | Medium | Smoke credits agents whose arms never executed |
| B15 | Medium | Container discovery bypasses execution timeouts |
| B16 | Medium | Cleanup aborts at the first project timeout |
| B17 | Medium | Pruning removes paused/restarting trial projects |
| B18 | Medium | Transport startup exceeds its timeout and reconnect budget |
| B19 | Medium | Contention includes jobs on other Docker hosts |
| B20 | Medium | Harbor problem validation ignores concurrency limits |
| B21 | Medium | Native parity reads unverified working-tree content |
| B22 | Medium | Submission dependencies ignore environment markers |
| B23 | Medium | Concurrent provisional promotions overwrite complete Assessments |
| B24 | Medium | Special files hang snapshot hashing and assessment |
| B25 | Medium | Unreadable directories silently disappear from evidence |
| B26 | Medium | McNemar calculation crashes at 1,024 discordant pairs |
| B27 | Medium | Development analyses remain machine-labelled citable |
| B28 | Medium | Quoted shell text falsely satisfies process checks |
| B29 | Medium | Gate evidence reads escaping paths before validation |
| B30 | Medium | Shadow autofix can change the original workspace through symlinks |
| B31 | Medium | Cross-origin requests can write reviewer annotations |
| B32 | Medium | Malformed URLs escape server error handling |
| B33 | Medium | Missing Quality components crash projection for valid zero scores |
| B34 | Medium | UI presents unavailable evidence as a clean result |
| B35 | Medium | Stale Quality links display another experiment |
| B36 | Medium | Automated version bumps leave the lockfile stale |
| B37 | Medium | Hook installer omits the advertised pre-push gate |
| B38 | Medium | Agent validation accepts nonexistent CLI overrides |
| B39 | Low | Recursive YAML aliases crash experiment loading |
| B40 | Low | Unhashable YAML keys bypass validation diagnostics |
| B41 | Medium | MCP recorder confuses server requests with client responses |
| D01 | Medium | Final-tree gate metrics conflict with last-decided-run semantics |
| D02 | Medium | Scoring reference describes the retired scoring system as active |
| D03 | Medium | Contributor guides prescribe removed workflows |
| D04 | Medium | Environment reference advertises inert controls |
| D05 | Low | Documented Claude credential alias is rejected |
| D06 | Low | UX knowledgebase describes retired data/API contracts |
| D07 | Low | Resolver documentation promises an unpinned report digest |
| D08 | Low | Smoke documentation disagrees about grader matching |
| D09 | Low | Pack release instructions overstate image-hash inputs |
| D10 | Low | Claude model restriction contradicts supported examples |
| D11 | Low | UI recovery command uses a rejected variable |
| D12 | Low | Nested UI build instruction targets another checkout |
| D13 | Low | Performance CI uploads the wrong evidence directory |

## Implementation findings

### B01 — High: candidate code executes on the assessor host through Ruff module shadowing

- **Locations:** `orchestrator/src/raidar/scoring/code_quality/quality.py:243-248`; `measurement.py:72-81,99-105`; `contract.py:32-41` in the same package.
- **Trigger and impact:** A candidate snapshot contains `ruff.py` or a `ruff/` package. The installed-version check runs outside that snapshot, but the subsequent `python -m ruff` runs with the snapshot as its working directory and no Python `-I`. Candidate code takes precedence over the installed module, executes with assessor permissions and inherited environment, and can fabricate lint diagnostics.
- **Validation:** A harmless temporary `ruff.py` wrote a proof marker and printed `[]`. The exact process invocation and actual `_ruff` call executed it; the latter accepted the empty diagnostics after a genuine version precheck. Only the expected-version constant was patched in memory to match the installed Ruff for that probe. No sensitive data was accessed. Production `ToolEnvironment.run` forwards the provisioned interpreter without isolation; only `.tool` supplies `-I`.
- **Recommendation:** Apply isolated mode to both Ruff calls and minimize inherited environment; add a candidate-module canary. `measurement.py:3-9` incorrectly claims every process is isolated.

### B02 — High: component symlinks disclose hidden verifier content through an accepted lock

- **Locations:** `orchestrator/src/raidar/design/experiment/validation.py:126-130`; `lock.py:211-217`; `orchestrator/src/raidar/subject/treatment/catalog/workspace.py:112-119`.
- **Trigger and impact:** A local task lives under `tasks/forge`, and a selected component such as `components/AGENTS.md` links to `tasks/forge/tests/test_hidden.py`. Exclusion compares lexical path strings; hashing permits the symlink when its target remains inside the experiment. Compilation reads the target and delivers its contents as agent instructions.
- **Validation:** In a temporary experiment, production `load_experiment`, component checks, and `build_lock` with default resolvers all succeeded. The compiled `AGENTS.md` bytes equalled the hidden file. No agent ran.
- **Recommendation:** Check resolved ownership for selected files and descendants, including symlinked parents and task aliases. This violates F4/N2 in `docs/reboot/requirements.md:16,34` and the validation function's exclusion promise.

### B03 — High: the declared agent network allowlist has no enforcement path

- **Locations:** `orchestrator/src/raidar/design/experiment/arm.py:159`; `orchestrator/src/raidar/subject/treatment/definition/compiled.py:127`; `orchestrator/src/raidar/engine/trial/config.py:68-73`; `orchestrator/src/raidar/design/problem/rewrite.py:98-106`.
- **Observed gap:** Component egress hosts are collected into `network_egress`, but no production consumer applies that field. Task rewriting preserves source network policy and the trial config supplies no replacement. For the normal public default, there is no Raidar allowlist.
- **Validation:** Reference tracing found the declaration and producer but no consumer. The installed Harbor model's default is `network_mode=PUBLIC`, `allowed_hosts=None`; Harbor provides explicit allowlist support. No actual network traffic was generated.
- **Impact and recommendation:** The active N2 guarantee at `docs/reboot/requirements.md:34` and the allowlist claim in treatment `manifest.py:47` overstate isolation. Resolve and enforce baseline plus component destinations through Harbor, with denied/allowed destination canaries; explicitly document the gap until then.

### B04 — High: existing Docker contexts bypass explicit remote trust approval

- **Location:** `orchestrator/src/raidar/engine/target/config.py:181`.
- **Trigger and impact:** A named Docker context points to a remote SSH/TCP daemon but the target omits `trusted`. Trust defaults to true whenever Raidar's own transport block is absent. Docker contexts can themselves be remote, so credentials and hidden tests can reach an endpoint never explicitly marked trusted.
- **Validation:** A pure parser probe with an existing remote-style context produced `trusted=True` and passed `require_trusted`; no endpoint inspection establishes locality. No Docker command ran.
- **Recommendation:** Require explicit trust for unknown contexts, or establish locality from their endpoint before defaulting trust. Reconcile with `README.md:27` and `docs/reboot/adr-002.md:32`.

### B05 — High: snapshot digest serialization permits trivial collisions

- **Location:** `orchestrator/src/raidar/contract/snapshot.py:72-79`; acceptance guard at `orchestrator/src/raidar/scoring/framework/trial_inputs.py:221-232`.
- **Cause and proof:** Records concatenate path, NUL, kind, arbitrary bytes, and NUL without lengths or escaping. One file `a.py = b'x=1\n\x00b.py\x00Fy=2\n'` hashes identically to two files `a.py = b'x=1\n'` and `b.py = b'y=2\n'`. The first fails Python compilation; both replacement files compile. After replacement, production `_snapshot(..., original_digest)` accepted the changed tree.
- **Impact:** Snapshot integrity checks and content-addressed pre-fix/coverage caches can alias materially different code without a cryptographic attack.
- **Recommendation:** Use an unambiguous serialization, such as length-prefixed records or canonical metadata with per-file content hashes. Changing this identity-bearing source requires the repository's archive/release/relock procedure.

### B06 — High: a failed revalidation can leave the old validated report authoritative

- **Location:** `orchestrator/src/raidar/design/problem/validator.py:247-249`.
- **Trigger:** A previously validated problem is rechecked, a real reference/determinism failure is found, and some formerly executed check is skipped, for example because no native runner is supplied.
- **Observed result:** The skipped-check preservation branch routes even an `excluded` report into the attempt cache. The old committed `validated` report remains usable by problem admission.
- **Validation:** Temporary-store probe: `new_verdict=excluded`, `routes_to_attempt=True`, `committed_still_usable=validated`.
- **Recommendation:** Give actual exclusion failures precedence over preservation of stronger positive evidence. The resolver's rule that any failing problem is excluded is currently not upheld.

### B07 — High: Quality chooses same-snapshot Assessments from unrelated scoring settings

- **Location:** `orchestrator/src/raidar/evaluation/review/quality/sources.py:213-224`.
- **Trigger and impact:** An Assessment store contains the same problem/snapshot under different scorer configurations, implementations, verifier inputs, or errata. `StoreIndex.find` chooses the latest by problem and snapshot rather than the exact locked assessment key. Quality statistics and verdicts can silently change after unrelated rescoring and disagree with analysis/main projection.
- **Validation:** Two synthetic same-snapshot assessments with distinct configurations selected `other-config-key`. The main projection's `AssessmentLookup` uses exact identity instead.
- **Recommendation:** Reuse exact-key lookup with the selected lock's rescore configuration; missing exact evidence must remain missing.

### B08 — High: review projections aggregate evidence that analysis refuses to pool

- **Locations:** `orchestrator/src/raidar/evaluation/review/quality/experiment_view.py:545-563`; `projection/experiment_epoch.py:245-269`; Quality discovery at `quality/sources.py:173-187`.
- **Trigger and impact:** Jobs share an experiment/lock but differ in mechanics epoch, implementation, grader recipe, effective limits or target, or contain modified sealed trial records. Both review pipelines group without the analysis provenance/tamper validation. They can combine or supersede incompatible trials and present unsupported statistics.
- **Validation:** A cloned synthetic fixture with the same lock but two execution targets produced one Quality experiment containing both jobs with `notes=()`. Source tracing confirms no equivalent of analysis `check_jobs`/`check_pooling`; record verification is also absent at this boundary.
- **Recommendation:** Apply shared integrity/provenance checks before aggregation, or split evidence and withhold incompatible comparisons with reasons. `quality/experiment_view.py:3-5` incorrectly equates lock-only grouping with the analysis checks.

### B09 — High: binary aggregate verdict can contradict its positive effect and interval

- **Locations:** `orchestrator/src/raidar/evaluation/analysis/effects.py:580-615,367-371`.
- **Cause:** Mean and CI weight problems equally, but binary significance/direction pool raw discordant pairs. Unequal retained pair counts give these outputs different estimands.
- **Validation:** Through `analyse`, problem one retained 450 all-positive treatment pairs from 550 planned; problem two retained 550 pairs with 530 negative and 20 ties. Exclusions were 9.09%, below the default limit. Output: `mean_delta=+0.0181818`, 95% CI `[+0.0103314,+0.0260323]`, but `verdict=worse`, McNemar `p=0.01158`.
- **Impact and recommendation:** A supported analysis tells the user the opposite direction to its displayed equal-weight effect. Test significance and direction for the same estimand; label pooled McNemar separately if retained. Equal weighting is specified at `docs/reboot/design-memo.md:112`.

### B10 — High: a dirty pre-agent leak scan does not prevent agent execution

- **Location:** `orchestrator/src/raidar/grading/guard.py:40-43`.
- **Trigger and impact:** The scan successfully detects hidden tests in the agent environment. The hook writes `clean=False` and returns; Harbor continues into the agent phase. Later reward invalidation cannot undo test disclosure to the model or cost incurred on a known-contaminated trial.
- **Validation:** Existing fake trial/environment with `/tests/conftest.py` produced `hook_completed_normally=True`, `recorded_clean=False`. The inspected Harbor path awaits this hook and then executes the agent.
- **Recommendation:** Preserve the report and raise on a dirty scan. Retain later invalidation for contamination discovered afterward. Existing coverage checks invalidation, not prevention of known exposure.

### B11 — High: offline coverage cache reuses parity from another grading run

- **Locations:** `orchestrator/src/raidar/grading/coverage/offline.py:217-219`; `coverage/store.py:43-60`; `orchestrator/src/raidar/scoring/code_quality/coverage_reader.py:50-78`.
- **Trigger and impact:** Trials share bundle, checkpoint and snapshot but differ in grading outcome, environment or timeout. The cache key covers only the former three, while cached content includes baseline/fork evidence and parity. A later trial consumes the old eligibility rather than comparing coverage with its own grade, potentially omitting flipped-case exclusions.
- **Validation:** Populate a synthetic deferred-coverage cache, then change one grading case from pass to fail without changing the snapshot. Production returned `stored` with eligible parity; a fresh check returned `eligible=False`, `outcome_mismatch`.
- **Recommendation:** Separate reusable coverage execution from trial-relative parity and recompute parity for each trial; bind execution environment/settings into run identity. Pre-fix grades at `grading/prefix/grade.py:158-160` similarly omit timeout/environment and need corresponding identity handling.

### B12 — Medium: execution code changes do not invalidate smoke or pooling evidence

- **Locations:** `orchestrator/src/raidar/engine/job/provenance.py:33-40`; `design/experiment/lock.py:138-165`; `engine/health/admission.py:59-79`; `evaluation/analysis/pooling.py:97-120`.
- **Trigger:** Commit a behavior change confined to execution, target, health, liveness or reaper code after a clean smoke. The recorded runner toolchain is only `spec_source_digest`, excluding those owners; admission ignores commit/dirty and pooling ignores commit.
- **Validation:** Distinct clean runner commits with equal spec hashes produced admission `mismatches=[]`; the digest closure excludes `engine/`.
- **Impact and recommendation:** Old smoke remains valid and clean jobs from different execution implementations remain poolable despite the “same code” promise. Record and compare a semantic execution digest separately from spec identity.

### B13 — Medium: the advertised multi-agent smoke cannot bootstrap

- **Locations:** `Makefile:619-623,664-665`; `orchestrator/src/raidar/engine/health/admission.py:142-174,186-203`; `README.md:182-191`.
- **Cause:** `make smoke-agents` runs a benchmark-kind experiment, requiring prior smoke, but only smoke-kind jobs qualify. Separate healthy per-agent smokes also do not satisfy the all-agent search: a single candidate must cover every wanted agent. The failure recommends the same blocked target.
- **Validation:** Matching healthy Codex and Claude smoke fixtures still yielded “no clean smoke job matches this benchmark experiment.” The benchmark output itself can never become a smoke candidate.
- **Recommendation:** Provide a genuine multi-agent operational smoke and/or combine verified per-agent coverage. Keep benchmark identity distinct.

### B14 — Medium: smoke admission credits unexecuted agents

- **Location:** `orchestrator/src/raidar/engine/health/admission.py:39-67,129-160`.
- **Trigger and impact:** Run only one arm of a multi-agent smoke. Coverage is derived from every arm in its lock, while cleanliness checks only selected records. An agent/model never exercised can be admitted to measurement.
- **Validation:** A healthy one-record fixture whose lock listed Codex and Claude admitted a Claude-only non-smoke job; only the baseline trial existed.
- **Recommendation:** Derive coverage from selected, validated executed trials and require evidence for each requested agent.

### B15 — Medium: container discovery is outside the command timeout

- **Location:** `orchestrator/src/raidar/engine/trial/environment.py:192-205,243-246`.
- **Trigger and impact:** Uncached `docker ps` hangs. Its `communicate()` has no timeout or cancellation cleanup and runs before the caller's exec timeout is applied. Outer cancellation can leave the Docker subprocess unreaped.
- **Validation:** A hanging fake discovery process remained blocked with `timeout_sec=0` until a separate 50 ms deadline; its termination flag remained false.
- **Recommendation:** Include discovery in the command deadline and terminate/reap on timeout/cancellation.

### B16 — Medium: one cleanup timeout prevents all subsequent cleanup

- **Location:** `orchestrator/src/raidar/engine/trial/cleanup.py:104-107`; timeout at `36-39`.
- **Trigger and impact:** The first stale project's Compose down exceeds 60 seconds. `TimeoutExpired` escapes instead of joining collected failures, so later orphan projects are never attempted. This contradicts the function's explicit all-project promise.
- **Validation:** A two-project fake with the first timing out recorded only its first project as attempted.
- **Recommendation:** Accumulate timeout/transport failures per project, continue, then report the aggregate.

### B17 — Medium: target pruning destroys paused or restarting projects

- **Locations:** `orchestrator/src/raidar/engine/trial/cleanup.py:55,68`; `orchestrator/src/raidar/cli/target.py:189`.
- **Trigger and impact:** Liveness is defined only as `state == running`. Paused and restarting containers are selected as stale and removed by Compose down, potentially destroying recoverable active work.
- **Validation:** Pure project-selection probe selected paused, restarting and created states while excluding only running.
- **Recommendation:** Prune confirmed terminal states conservatively and recheck before deletion.

### B18 — Medium: transport startup exceeds its advertised timeout

- **Location:** `orchestrator/src/raidar/engine/target/transport.py:177-186`; health at `135-140`; outer reconnect at `guard.py:88-110`.
- **Cause and validation:** The loop counts only sleeps, ignoring ten-second health probes. A fake-clock/probe simulation yielded 41 probes and **430.5 seconds** for a nominal 20-second timeout.
- **Impact:** A single startup can exceed the default 300-second reconnect budget before the outer guard checks it again.
- **Recommendation:** Use a monotonic deadline and cap every probe to remaining outer/inner time.

### B19 — Medium: contention reports count jobs on unrelated hosts

- **Location:** `orchestrator/src/raidar/engine/perf/contention.py:113-155`; report at `perf/report.py:89-116`.
- **Trigger and validation:** Overlapping siblings under one jobs root are counted without target comparison. One trial on `mbp` plus three on `remote-host` produced `peak_including_other_jobs=4` and listed the remote job as contention.
- **Impact and recommendation:** Idle-host samples can be classified as high-contention and budget breaches misattributed. Filter by resolved daemon/target identity; missing identity is unknown, not proof of shared contention.

### B20 — Medium: Harbor validation ignores requested/target-limited concurrency

- **Locations:** `orchestrator/src/raidar/design/problem/validator.py:82-89`; `harbor_engine.py:127-133`; CLI clamp at `orchestrator/src/raidar/cli/problem.py:254`.
- **Cause and validation:** Effective concurrency is not forwarded and all repeats enter `asyncio.gather`. A mocked `REPEATS=5, CONCURRENCY=1` run reached five simultaneous trials.
- **Impact and recommendation:** Validation can overload the target and induce unreliable failures. Forward the effective limit and semaphore-bound execution.

### B21 — Medium: native parity uses unrecorded working-tree task bytes

- **Locations:** `orchestrator/src/raidar/design/problem/validator.py:98-100`; `source.py:98-102`; `native.py:45-50`.
- **Trigger:** The source checkout has the correct HEAD but local modifications/additions inside the problem. Native staging copies that directory wholesale without the `verify_clean()` used by import at `importer.py:217-219`.
- **Impact:** A report attributes parity to a pinned problem while actually comparing against modified tests/configuration. The native-runner revision does not identify those problem-source edits.
- **Validation:** Complete production call-path inspection; no evaluator was launched.
- **Recommendation:** Verify source cleanliness or stage directly from the pinned tree.

### B22 — Medium: dependency provisioning ignores PEP 508 environment markers

- **Location:** `orchestrator/src/raidar/grading/submission_deps.py:116-129`.
- **Trigger and validation:** `PyYAML==6.0.3; python_version < "2.0"` was reported as provided on the Python 3 runtime, although its marker evaluates false. Resolution checks name/version but not `requirement.marker`.
- **Impact and recommendation:** A submission may pass by importing dependencies normal installation would omit. Evaluate markers against the submission/grader runtime, not an unrelated host environment.

### B23 — Medium: provisional Assessment promotion races overwrite complete results

- **Location:** `orchestrator/src/raidar/scoring/framework/store.py:178-194`.
- **Trigger and validation:** Two writers read one provisional object, then independently promote differing complete results. A barrier-controlled two-thread probe returned `superseded` for both values 1 and 2; only one survived and only the provisional object was archived.
- **Impact and recommendation:** The promised conflict detection for differing complete Assessments is bypassed. Serialize promotion per key across processes, re-read under the lock, and preserve conflicting complete results.

### B24 — Medium: special files hang hashing and offline assessment

- **Locations:** `orchestrator/src/raidar/scoring/framework/inventory.py:330-344`; `orchestrator/src/raidar/contract/snapshot.py:76-78`.
- **Trigger and validation:** A snapshot contains FIFO `generated.py`. Both hashing and inventory assume non-symlinks are regular files and call `read_bytes()`. Separate bounded child probes blocked and required termination after one second.
- **Impact and recommendation:** Agent-controlled artifacts cause unbounded host reads. Check `lstat` file kind before opening; reject unsupported evidence explicitly.

### B25 — Medium: unreadable subtrees disappear without invalidating evidence

- **Locations:** `orchestrator/src/raidar/scoring/framework/inventory.py:209-223`; `orchestrator/src/raidar/contract/snapshot.py:49`.
- **Trigger and validation:** `os.walk` has no error handler. After a temporary source directory was made unreadable, inventory returned `files=0, eligible=True, invalid=0`; digest identity changed although the source bytes had not. Permissions were restored afterward.
- **Impact and recommendation:** Quality measurements silently shrink their evidence, contradicting the inventory contract. Fail closed on traversal errors and record a concrete inventory issue.

### B26 — Medium: exact McNemar overflows on supported large analyses

- **Location:** `orchestrator/src/raidar/evaluation/analysis/stats.py:203-204`.
- **Validation:** Both `mcnemar_exact(0,1024)` and `(512,512)` raised `OverflowError`; `(0,1023)` returned a finite probability. Intermediate `2.0**discordant` and integer-to-float conversion are unstable.
- **Impact:** Attempts have no upper bound and discordances accumulate across problems, so a legitimate large confirm analysis can abort.
- **Recommendation:** Use a numerically stable binomial-tail calculation; cover balanced and extreme tables above the boundary.

### B27 — Medium: development output is positively labelled citable

- **Location:** `orchestrator/src/raidar/evaluation/analysis/report.py:466`; contradictory warning at `markdown.py:635-636`.
- **Trigger and validation:** A non-smoke analysis using allowed development data, including `agent_override=oracle` and truncated checkpoints, produced `citable=True`. The flag checks kind alone.
- **Impact and recommendation:** Machine consumers receive citation eligibility for data explicitly described as not arm evidence. Make development reports non-citable and distinguish their explanation from operational smoke.

### B28 — Medium: quoted command text falsely satisfies process checks

- **Location:** `orchestrator/src/raidar/scoring/process_checks/trace_events.py:102,306-309`.
- **Trigger and validation:** After an edit, `echo 'diagnostic; pytest -q'` was treated as an executed test. A full scorer probe returned `ok:1.0` for tests-before-finish, tests-any, tests-after-last-edit and compliance rate although only echo executes.
- **Impact and recommendation:** Deterministic compliance signals become false positives. Parse shell quoting/command structure or report insufficient evidence when execution cannot be established.

### B29 — Medium: escaping gate log paths are read before containment validation

- **Location:** `orchestrator/src/raidar/scoring/gate_effort/evidence.py:42-46`.
- **Trigger and validation:** `run.log=../outside.log` joined to the trace directory read a harmless sibling file and built evidence referencing it. Absolute paths and symlink escapes reach the same unguarded read.
- **Impact:** Later result validation rejects escaping evidence, but only after the host filesystem read. This does not establish successful downstream disclosure; it establishes a read-boundary violation.
- **Recommendation:** Use the existing `records._inside` containment check before filesystem access.

### B30 — Medium: shadow autofix can mutate the original workspace through symlinks

- **Location:** `orchestrator/src/raidar/subject/treatment/runtime/autofix_run.py:81-95`.
- **Trigger:** The workspace contains a symlink to a source file in the original workspace. Tar preserves the link in the supposed throwaway copy; a fixer explicitly operating on that copied link follows it back to original bytes.
- **Validation:** A temporary copy using the production copy script plus installed Ruff `--fix` reported `copy_ok=True, workspace_changed=True`. Existing regular-file shadow-copy tests passed and do not cover the symlink case.
- **Impact and recommendation:** A shadow measurement can apply treatment changes to the baseline despite its non-mutating guarantee. Ensure writable paths in the copy cannot resolve back into the original, and test absolute/escaping symlinks. This is conditional on the fixer following the selected link, not a claim that every default traversal follows it.

### B31 — Medium: cross-origin mutation requests can persist reviewer annotations

- **Locations:** `review-surface/server.mjs:192-196,237-254`; related labels at `quality-api.mjs:27-60`.
- **Trigger and validation:** The exact handler, with HTTP transport mocked and temporary storage, accepted `Origin: https://untrusted.example`, `Content-Type: text/plain`, parsed JSON, returned 200 and persisted an annotation. No valid run was required.
- **Impact:** A hostile page whose request reaches the loopback service can forge reviewer notes. Lack of CORS response headers does not prevent simple POST side effects. Actual browser reachability was not exercised; private-network protections can constrain it.
- **Recommendation:** Validate Origin/Host and JSON content type on mutations, with a same-origin CSRF mechanism.

### B32 — Medium: malformed request URLs can terminate the review service

- **Location:** `review-surface/server.mjs:291-293`.
- **Trigger and validation:** URL construction occurs outside the async listener's `try`. `req.url='//[/'` rejected with `ERR_INVALID_URL`; an unawaited callback in a bounded Node child exited 1.
- **Impact and recommendation:** A malformed request can stop the service. Parse within guarded error handling, respond 400, and ensure listener rejections are handled. Socket binding was unavailable; the actual handler was tested through mocked transport.

### B33 — Medium: valid zero scores with missing quality crash projection

- **Location:** `orchestrator/src/raidar/evaluation/review/quality/experiment_view.py:407-449`.
- **Trigger and validation:** `scb-canonical@2` legitimately gives `C=0,S=0` despite unavailable quality components. `_eligible_components` admits S, but sensitivity indexes missing component keys. A synthetic zero-correctness fixture with quality assessments removed raised `KeyError: 'V'` from `build_quality_projection`.
- **Impact and recommendation:** Unsuccessful/partially assessed trials prevent all Quality output. Retain valid headline zeros but require complete measured inputs separately for ablation/correlation and expose gaps.

### B34 — Medium: missing evidence is rendered as affirmative cleanliness

- **Location:** `review-surface/src/quality/components/EvidenceDrilldown.tsx:72-75` (messages at `10-15`).
- **Trigger and validation:** The component receives no status, only evidence/exclusions. Rendering empty unmeasured evidence asserted “No changed symbol was executed by a failing regression test.” QualityStrip allows unavailable/not-applicable cells to open it.
- **Impact and recommendation:** Users can mistake absence of measurement for evidence of no regression. Pass status/reason and reserve clean assertions for measured results; handle mixed checkpoint status explicitly.

### B35 — Medium: stale deep links silently show another experiment

- **Location:** `review-surface/src/quality/QualityPage.tsx:180-181`.
- **Trigger and validation:** An explicit missing experiment/lock query falls back to `list[0]`. Rendering the actual component with mocked query data showed an unrelated experiment and primary outcome while the URL still named the requested missing target.
- **Impact and recommendation:** Saved evidence links can misattribute results after reprojection. Default only when no explicit target was supplied; otherwise show not-found and require selection.

### B36 — Medium: automated version bumps commit a stale project lock

- **Locations:** `scripts/release/bump-version.py:287-291`; `.github/workflows/version-changelog.yml:39-43,62-68`; editable package identity in `orchestrator/uv.lock:1929-1931`.
- **Trigger and validation:** The script changes pyproject version and changelog but neither it nor workflow refreshes the lock. In a temporary manifest/lock copy, changing 0.15.0 to 0.15.1 made `uv lock --check --offline` exit 1 with lock-needs-update.
- **Impact and recommendation:** Release commits fail configured locked-sync hooks or retain stale installed version metadata under frozen sync. Refresh and validate the lock in the release transaction.

### B37 — Medium: the provided installer does not install the full pre-push gate

- **Locations:** `scripts/setup/install-pre-commit.sh:17`; `.pre-commit-config.yaml:18-24`; `README.md:213`.
- **Validation:** The installer runs bare `pre-commit install`. Loading the actual config reports `default_install_hook_types=['pre-commit']`, while full `make quality` is assigned only to pre-push.
- **Impact and recommendation:** Fresh installations omit promised structure/lock/ledger/smoke-shape push enforcement. Install both hook types or declare both defaults.

### B38 — Medium: CLI override validation accepts nonexistent executables

- **Locations:** `orchestrator/src/raidar/subject/agent/adapters/base.py:43-50`; `adapters/harbor_cli.py:36-39`; `orchestrator/src/raidar/cli/agent.py:71`.
- **Trigger and validation:** A nonempty nonexistent `CODEX_CLI_PATH` is returned without executable checks. With synthetic credential presence, `CodexCliAdapter.validate()` succeeded while the path did not exist. Later auth setup passes it to subprocess and can raise uncaught `FileNotFoundError`.
- **Impact and recommendation:** Public agent validation gives false assurance. Resolve/check override existence and executable permission and return a configuration diagnostic.

### B39 — Low: cyclic YAML aliases crash before useful validation

- **Location:** `orchestrator/src/raidar/design/experiment/loader.py:158-181`.
- **Validation:** `loop: &loop [*loop]` caused `RecursionError`; alias expansion has no ancestor-cycle detection.
- **Impact and recommendation:** Malformed input escapes normal `SpecValidationError`. Detect cycles and return source-located diagnostics.

### B40 — Low: unhashable YAML keys escape the error contract

- **Location:** `orchestrator/src/raidar/design/experiment/loader.py:43-55`.
- **Validation:** A sequence mapping key, `? [a,b]\n: value`, raised raw `TypeError: unhashable type: 'list'` from duplicate-key membership checking.
- **Impact and recommendation:** Authoring errors crash validate/lock consumers instead of readable diagnostics. Convert unsupported keys to a marked YAML constructor error.

### B41 — Medium: MCP discovery confuses server requests with client responses

- **Locations:** `orchestrator/src/raidar/subject/treatment/runtime/mcp_shim.py:51-54,63-70`; discovery rejection at `orchestrator/src/raidar/subject/treatment/catalog/harness.py:418-425`.
- **Trigger:** While client `tools/list` ID 1 is pending, the server sends `ping` ID 1. Bidirectional request-ID namespaces are independent. The recorder treats every server message as a response and consumes the pending client entry without checking message kind.
- **Validation:** A pure recorder probe sent client tools/list, server ping, client ping response, then server tools result. It attributed the ping to tools/list and discovered no tools despite the valid listing.
- **Impact and recommendation:** A compliant MCP exchange can fail discovery and make the trial infrastructure-invalid. Distinguish requests/notifications/responses and correlate responses only with pending requests in the opposite direction.

## Documentation and contract findings

### D01 — Medium: final-tree gate measurements disagree with last-decided semantics

- **Locations:** `orchestrator/src/raidar/scoring/gate_effort/findings.py:101-111`; `step_added_counts.py:32-40`; `orchestrator/tests/test_gate_effort_cross_mode.py:6-7`.
- **Evidence:** After a refused gate followed by a fix session that times out after edits, the scorer correctly makes `gate_clean_at_step_end` insufficient, but returns the earlier count as `step_end_blocking`, final/net variants and the next checkpoint baseline. A full synthetic scorer returned these as `ok=3` although the final tree was unchecked.
- **Contract distinction:** The manifest explicitly says “last decided gate run”; tests/documentation promise the final tree. Therefore this entry does **not** assume last-decided-run implementation alone is wrong.
- **Required decision:** Define historical versus final-tree counts clearly. Final-tree/next-baseline measurements need unavailable status after unchecked edits; historical counts can remain with accurate names and explanations.

### D02 — Medium: the scoring reference presents retired scorers as current

- **Locations:** `docs/references/metrics.md:3-13,40-50`; actual registry `orchestrator/src/raidar/scoring/catalog/builtin.py:27-36`.
- **Evidence and consequence:** Instructions prescribe scenario authoring, weighted category scorers and nonexistent `raidar/scorers/`, listing design-to-code/python-code-task/resource-efficiency as active. Current experiments use correctness, code-quality, resource-usage, process-compliance and gate-effort. Following the page cannot create a valid current experiment.
- **Correction:** Replace with the assessment contract or explicitly archive it and point to root README.

### D03 — Medium: contributor guides prescribe removed authoring and agent paths

- **Locations:** `docs/references/new-scenario.md:224-264`; `new-harness.md:7-10,77-87`; `orchestration-flow.md:7-18,60-98`.
- **Evidence and consequence:** Active instructions use scenario/version/matrix trees, removed scenario/runtime-stack/experiment-run targets and nonexistent agent/harness modules. `README.md:20` retires them; current entrypoints are experiment lifecycle targets and `subject/agent/`.
- **Correction:** Replace or mark historical rather than directing maintainers into prohibited parallel structures. This concerns integration workflows, distinct from D02's scoring contract.

### D04 — Medium: the environment reference advertises settings with no consumers

- **Location:** `docs/references/env-var-lexicon.md:84-127`.
- **Evidence and consequence:** The page promises timeout/gate/scoring behavior through `EVAL_TIMEOUTS__*`, `EVAL_RESOURCE_EFFICIENCY__*` and other families via nonexistent `raidar/config.py`. Current source/Makefile have no consumers for the cited timeout/resource families. Users may believe deadlines or scoring changed when nothing changed.
- **Correction:** Remove retired controls and document supported settings with their actual consuming owners.

### D05 — Low: documented Claude credential alias fails preflight

- **Locations:** `docs/references/env-var-lexicon.md:17-18`; legacy declaration `orchestrator/src/raidar/subject/agent/harness.py:97-108`; execution `engine/job/credentials.py:25-27,62-68`.
- **Validation and consequence:** Synthetic presence of only `CLAUDE_CODE_API_KEY` yielded missing-credential failure, contrary to the documented alias. Execution supports other named credentials.
- **Correction:** Align documentation/metadata or intentionally support the alias end to end.

### D06 — Low: UX source-of-truth pages describe a retired projection

- **Locations:** `docs/review-surface-ux-kb/data/source-artifacts.md:12-34`; `data/api.md:21`; `pages/experiments.md:36,41-42`.
- **Evidence:** Pages claim benchmark/scenario artifact lineage, `revision_diffs`, and `benchmark-fixture-synthetic`. Current builder at `review-surface/scripts/build-review-data.mjs:90-123` consumes schema-v2 projection and emits `arm_diffs`; fixture target is `review-fixture-synthetic`.
- **Consequence and correction:** Integration/debugging guidance is incompatible. June timestamps suggest historical material, but the bundle still presents itself as source of truth; explicitly archive it or update its current contract claims.

### D07 — Low: resolver claims a validation-report digest is pinned

- **Locations:** `orchestrator/src/raidar/design/problem/resolver.py:13-14`; `validation.py:391-401`.
- **Contradiction:** Resolver documentation says the lock records report verdict and digest; `lock_metadata` deliberately omits report bytes and returns verdict/not-run metadata.
- **Consequence and correction:** Readers overestimate evidence cryptographic binding. Describe manifest identity and report metadata accurately.

### D08 — Low: smoke prerequisite documents disagree about grader recipes

- **Locations:** `docs/reboot/mechanics-v1-design.md:118`; `docs/reboot/adr-002.md:165`; `orchestrator/src/raidar/engine/health/admission.py:59-67`.
- **Contradiction:** The design says pack and grader recipes must match; ADR and code compare pack only, though provenance records both.
- **Required decision:** Align the design with intended policy, or implement grader matching if required. A policy disagreement is established; grader matching is not assumed mandatory without resolving it.

### D09 — Low: pack release instructions overstate image tag inputs

- **Locations:** `AGENTS.md:25`; `orchestrator/src/raidar/subject/environment/python/image.py:86-100`; `orchestrator/tests/test_python_pack_image.py:170-177`.
- **Validation:** Instructions say any parsed pack field changes the image tag. In-memory changes to `entry_file_template` and `task_environment.compose_main_service` left it unchanged; the test explicitly requires task-binding changes not to alter image identity.
- **Consequence and correction:** Release/revalidation decisions can follow the wrong trigger. Distinguish exact image-hash inputs from task-binding contract changes or deliberately revise identity policy.

### D10 — Low: Claude model policy contradicts supported examples

- **Locations:** `AGENTS.md:81`; `README.md:186`; `orchestrator/src/raidar/subject/agent/adapters/claude_code_cli.py:16-27`.
- **Validation:** AGENTS says all Claude trials are Haiku without reasoning effort; README selects Sonnet. The adapter accepted Sonnet and Opus with supported effort in pure validation.
- **Required decision:** State whether Haiku is a default/minimal explore choice or an enforced restriction, then align instructions/examples/validation. No provider call was made.

### D11 — Low: UI's recovery command is explicitly rejected

- **Locations:** `review-surface/src/pages/ExperimentsPage.tsx:585`; `Makefile:55`.
- **Evidence and correction:** Empty-state guidance uses `REVIEW_RUNS_ROOT`, whose use fails the legacy-name guard. Recommend `REVIEW_JOBS_ROOT` so recovery works.

### D12 — Low: nested build instruction targets another checkout

- **Location:** `review-surface/src/pages/AGENTS.md:15`.
- **Evidence and consequence:** The command uses an absolute path to `review-surface/` in a sibling `raidar` checkout instead of this `raidar-reboot-impl` tree. It can rebuild another checkout or fail while leaving this UI stale.
- **Correction:** Use the root public `make review-surface-build` target and repository-relative instructions.

### D13 — Low: performance CI silently drops its job evidence

- **Locations:** `.github/workflows/perf-fixture.yml:61-62`; output at `orchestrator/tests/test_overhead_fixture_run.py:110-126`.
- **Evidence and consequence:** Workflow uploads `perf-fixture-out/runs`, but fixture writes `perf-fixture-out/jobs`. Missing artifacts are ignored, losing diagnosis evidence on success/failure.
- **Correction:** Upload jobs or the fixture root and surface unexpectedly missing output.

## Coverage and verification

| Reviewer | Responsibility | Completed validation |
|---|---|---|
| r01 | Job execution, provenance, credentials, detach | Identity probe; temporary process termination probe rejected a suspected detach bug |
| r02 | Trial planning, execution, collection, cleanup | Timeout and multi-project cleanup fakes |
| r03 | Health, smoke admission, liveness, performance | 81 focused tests; admission and cross-target probes |
| r04 | Targets, transport, trust, pruning | Parser, project-state and simulated deadline probes |
| r05 | Experiment schema, locking, compatibility | 57 focused tests; production symlink-lock and malformed-YAML probes |
| r06 | Problem import, validation, registry, parity | Temporary report-routing and concurrency probes; native call tracing |
| r07 | Scoring framework, store, inventory, metrics | 53 focused tests; race, FIFO and permissions probes |
| r08 | Correctness, code quality, scorer catalogue | Exact Ruff invocation and real `_ruff` canary |
| r09 | Process checks, gate effort, resource usage, judge boundary | 85 focused tests; full scorer and containment probes |
| r10 | Verifier, leak guard, offline grading, dependencies | 54 focused tests; guard, cache parity and marker probes |
| r11 | Analysis, pairing, statistics, ranking, pooling | 56 focused tests; aggregate reversal, overflow and citable probes |
| r12 | Main and Quality projections | Exact-key, zero-score crash and incompatible-job fixtures |
| r13 | Agent adapters/auth and Python pack | CLI-path, model-support and image-hash probes |
| r14 | Treatment runtime workflows and compilation | Two existing shadow tests; symlink/autofix and bidirectional MCP probes |
| r15 | Review HTTP and Quality APIs | Actual handlers under mocked HTTP; bounded Node rejection probe |
| r16 | React review/Quality journeys | Actual-component server rendering with synthetic inputs |
| r17 | Make, CLI, CI, release scripts and hooks | 31 focused tests; stale-lock/config/path checks |
| r18 | Current requirements, ADRs and execution contracts | Network field/reference trace and installed Harbor defaults |
| r19 | Reference and UX documentation | Current target/API checks; synthetic credential presence |
| r20 | Shared contracts, snapshot identity, ledger | 18 ledger tests; digest collision through production snapshot guard |

Focused-suite counts overlap and are not added into a claimed unique total. No full browser, live network allowlist, SSH recovery or real Docker enforcement test was performed. External-service behavior and exploit reachability beyond the documented probes remain unverified.

### Repository gate result and report-placement conflict

- `make quality` **did not pass**. Before this report was created, structure, smoke command shapes, current experiment locks, ledger, Ruff formatting/lint, Python quality/fan-out, 16 import contracts, Lizard and mypy passed. Pytest stopped with **559 passed, 16 skipped, 1 failed** at `tests/test_detach_job.py::test_a_recycled_pid_is_not_a_running_supervisor`.
- A focused environment probe established that this sandbox rejects execution of `ps` with `PermissionError`. `engine/job/detach.py:248-250` consequently falls back to treating a live PID as the supervisor, explaining why the test observes `running` rather than `died`. This is an observed gate failure under restricted process inspection, not a claim that ordinary macOS execution also fails. No fallback or test was changed to force a pass.
- After creating the user-requested root `codereview.md`, `make structure-check` **fails with `unexpected root file: codereview.md`**. The repository root-file allowlist conflicts with the requested report destination. The report remains in the explicitly requested location; the checker was not weakened or edited. Consequently a final `make quality` cannot pass even if process inspection becomes available until the report-placement/allowlist decision is resolved.
- A separate full run, `python -m pytest tests -q --tb=short` from `orchestrator/`, completed with **1,769 passed, 24 skipped, 3 failed in 431.65 seconds**. It did not measure coverage or substitute for the root gate. Failures were:
  - `test_detach_job.py::test_a_recycled_pid_is_not_a_running_supervisor`: blocked `ps` inspection, as above.
  - `test_jev_client_and_cache.py::test_requests_transport_timeout_bounds_the_whole_request_not_each_read`: the sandbox denied localhost socket binding with `PermissionError`.
  - `test_problem_validation_behaviors.py::test_native_evaluation_reduces_to_the_same_case_keys_as_the_oracle`: this direct test invocation inherited an unwritable default UV cache outside the workspace. A focused rerun with the repository's `UV_CACHE_DIR`, `XDG_CACHE_HOME` and `TMPDIR` passed (**1 passed**).
- These failures establish verification limits in this environment, not additional confirmed product defects. All 20 review assignments completed. The findings table and detail headings have 54 matching unique IDs; severity counts agree and the report has no trailing whitespace. `git diff --check` passed for tracked checkout changes.

## Rejected candidates and review limits

- Detached supervisor termination leaving its child alive was not retained: a safe macOS process probe showed the child terminated.
- Infisical prefix expansion was not retained: the preceding `env` correctly consumes the expanded assignment.
- No process-check import into dormant judge code was found; Jev policy remained off. No extra resource-usage or chart defect was established without evidence.
- Proposed fixes may affect identity-bearing sources, scorer releases or all experiment locks. This report authorizes none of those changes and does not mark existing job evidence repaired.

## Remediation status

The user subsequently authorized fixing every finding and explicitly approved preserving release tags in a local archive because this worktree cannot write the shared parent repository's Git objects. The implementation and focused regression work below is applied to the canonical working tree. This section supersedes the original review's statements about whether fixes were made; it does not rewrite its original evidence.

**Implementation accounting: all 54 findings addressed: 41 implementation findings and 13 documentation findings.** The user enabled full access and the three previously blocked skill corrections were applied and validated. Final repository-wide verification is recorded below. “Implemented” is not a claim that previously blocked live Docker/browser checks passed.

| ID | State | Implemented behavior / evidence owner |
|---|---|---|
| B01 | Implemented | Ruff runs in Python isolated mode with a constrained environment; module/package and environment shadowing canaries. |
| B02 | Implemented | Component ownership checks resolve files, parents, descendants and task aliases before hidden bytes can enter overlays. |
| B03 | Implemented | Materialized Harbor task/phase policies enforce provider/package/component destinations and preserve stricter policies. |
| B04 | Implemented | Unknown Docker contexts require explicit trust; trusted initialization/test definitions state it. |
| B05 | Implemented | Snapshot hashing uses unambiguous framing and content hashes; former binary-record collisions no longer alias. |
| B06 | Implemented | Actual failed revalidation replaces earlier positive admission evidence, even when other checks were skipped. |
| B07 | Implemented | Both review views use exact locked Assessment identity; grading evidence/runtime/plugin changes also alter input keys. |
| B08 | Implemented | Both projections share analysis provenance/integrity admission before aggregating; historical provenance is labelled. |
| B09 | Implemented | Binary aggregate verdicts use the same equally weighted estimand/interval as the displayed effect. |
| B10 | Implemented | A dirty pre-agent leak scan writes its report and prevents agent execution. |
| B11 | Implemented | Raw compatible coverage can be reused, while parity is rebound; cache identity includes grading semantics, runtime payloads, dependency versions and execution settings. |
| B12 | Implemented | Jobs record semantic execution-code identity; smoke admission and pooling compare it. |
| B13 | Implemented | Multi-agent health uses a distinct real smoke experiment and compatible per-agent coverage can combine. |
| B14 | Implemented | Smoke coverage derives only from selected, executed and validated agent arms. |
| B15 | Implemented | Container discovery shares command deadlines and reaps its subprocess on cancellation/timeout. |
| B16 | Implemented | Cleanup continues after per-project transport/timeout errors and reports aggregated failures. |
| B17 | Implemented | Only terminal container states are pruned; state is rechecked before destructive cleanup. |
| B18 | Implemented | Transport and reconnect use monotonic deadlines including probes and other subprocess work. |
| B19 | Implemented | Contention excludes different targets and exposes unknown identity separately. |
| B20 | Implemented | Harbor validation forwards and enforces effective concurrency limits. |
| B21 | Implemented | Native parity refuses dirty source bytes before staging. |
| B22 | Implemented | Dependency markers use facts from the target submission interpreter and fail closed on missing facts. |
| B23 | Implemented | Per-key interprocess locking serializes Assessment promotions and preserves conflicting complete results. |
| B24 | Implemented | Inventory and snapshot hashing reject special files before opening; hashing also protects file replacement races. |
| B25 | Implemented | Traversal failures explicitly invalidate evidence rather than silently shrinking inventories. |
| B26 | Implemented | Exact McNemar avoids intermediate float overflow; balanced/extreme large-table coverage. |
| B27 | Implemented | Development analyses are non-citable in both machine and human reports. |
| B28 | Implemented | Shell syntax, quotes, uncertain control flow, exits and Unicode offsets are handled without inventing test runs. |
| B29 | Implemented | Gate log containment is checked before host filesystem access. |
| B30 | Implemented | Shadow copies remap internal links and refuse unsafe/broken targets before autofix. |
| B31 | Implemented | Loopback Host, mutation Origin/Fetch Metadata and JSON content type are checked before writes. |
| B32 | Implemented | Malformed request URLs return controlled errors inside the guarded handler. |
| B33 | Implemented | Valid headline zeros remain usable; sensitivity/correlation require complete measured vectors. |
| B34 | Implemented | Drilldowns display measurement status/reasons and assert clean only for completely measured clean results. |
| B35 | Implemented | Missing explicit deep-link targets show unavailable state rather than an unrelated experiment. |
| B36 | Implemented | Release version transactions generate and verify the lock before publishing release files. |
| B37 | Implemented | Installer installs pre-commit and pre-push hooks. |
| B38 | Implemented | CLI overrides must resolve to executables; validation/auth setup return useful configuration errors. |
| B39 | Implemented | Cyclic YAML graphs produce readable validation diagnostics. |
| B40 | Implemented | Unsupported YAML mapping keys produce marked constructor diagnostics. |
| B41 | Implemented | MCP requests/responses are correlated in independent directional namespaces. |
| D01 | Addressed | Final-tree gate metrics/baselines become unavailable after unchecked edits; historical counts remain distinct. |
| D02 | Addressed | Scoring reference describes current Assessment/scorer ownership and missing evidence. |
| D03 | Addressed | Retired contributor guides are explicitly historical and point to the current root entrypoint. |
| D04 | Addressed | Environment reference documents supported controls and their actual owners. |
| D05 | Addressed | Documentation and credential metadata match accepted execution credentials; bearer-token semantics retained. |
| D06 | Addressed | Retired UX lineage/API material is explicitly historical rather than current source of truth. |
| D07 | Addressed | Resolver accurately describes validation metadata, without claiming a report digest is pinned. |
| D08 | Addressed | Documentation consistently describes pack-only recipe matching, execution-code identity and actual arm coverage. |
| D09 | Addressed | Pack guidance and the release skill describe actual image-hash inputs separately from task-binding fields. |
| D10 | Addressed | README and the smoke skill identify Haiku as the default, with validated alternatives supported. |
| D11 | Addressed | UI recovery uses REVIEW_JOBS_ROOT. |
| D12 | Already resolved | The obsolete nested instruction was deleted by pre-existing concurrent work; replacement guidance uses root targets. |
| D13 | Addressed | Performance CI uploads jobs and surfaces missing evidence. |

### Releases and generated locks

- Created 19 annotated preservation tags at pre-remediation commit `124d5af` in `var/tmp/codereview-release-archive.git`. This includes the 16 releases actually replaced and three additional preserved component releases. `git bundle verify var/tmp/codereview-releases.bundle` passed; the bundle contains complete history and the annotated tags. Canonical parent-repository tags were not written.
- Released scorers in place: `correctness@1 → @2`, `code-quality@3 → @4`, `resource-usage@1 → @2`, `process-compliance@6 → @7`, `gate-effort@10 → @11`.
- Released components in place: `continue-on-stall@2 → @3`, `guardrail-gate@4 → @5`, `review-loop@2 → @3`, `transient-retry@1 → @2`, `mcp-server@1 → @2`, `lint-config@1 → @2`, `rules-file@1 → @2`, `workspace-files@1 → @2`, `skills@1 → @2`, `harness-settings@1 → @2`, `stop-hook@1 → @2`. The last three are companion releases required because their raw fingerprint includes the shared manifest file changed by the MCP release. A raw-byte audit of all 14 components confirms 11 changed-and-bumped definitions and three unchanged definitions; evidence is in `var/tmp/codereview-component-release-audit.json`.
- Updated registrations, entrypoints, current experiment/scaffold references, current documentation and behavioral fixtures. Rebuilt editable package metadata using a standard offline build with cached backend constraints; no installed metadata was edited by hand. No runtime pack recipe/image release was made.
- Regenerated all four experiment locks, including new `lab/experiments/smoke/python__circuit_eval-cp1__pack-agent-health/experiment.yaml`. Existing experiment execution lock digests remain unchanged; the new smoke has its own identity. Assessment keys and execution-code provenance distinguish the new implementation.
- Existing cache entries whose execution identity is missing or different remain retained but are not trusted by new readers. Normal rescoring populates bound evidence where snapshots are compatible. The snapshot identity scheme changed intentionally: old job bytes and digest fields were not rewritten, and old-format evidence may require its archived implementation or new execution evidence. Existing jobs are not claimed repaired or revalidated.

To import the authorized local archive tags later from this repository root:

```sh
git fetch var/tmp/codereview-releases.bundle 'refs/tags/archive/*:refs/tags/archive/*'
```

### Skill corrections after the permission change

The earlier restricted session rejected writes to skill paths. After explicit user authorization and a full-access permission change, corrections were applied to `.agents/skills/raidar-release/SKILL.md`, `.agents/skills/raidar-smoke/SKILL.md`, and `.agents/skills/experiment-assessment/SKILL.md`. All three pass the skill validator. The release skill now describes actual image-tag inputs, the smoke skill describes Haiku as a default, and the assessment skill references `code-quality@4`. The assessment-skill reference is a consequence of this release, not an extra numbered original finding.

The saved patch is retained as an audit artifact; a reverse check verifies the approved changes are applied:

```sh
git apply --reverse --check var/tmp/codereview-patches/protected-skills.patch
```

### Integrated verification

Focused behavior suites passed across every changed subsystem, including adversarial isolation, process races, marker environments, provenance, exact Assessment keys and UI server-rendered behavior. Counts overlap; they are not summed.

- Latest full-access verification: **`make quality` passed**, with **1,973 passed, 12 skipped, 10 warnings in 690.79 seconds** and **96.33% coverage** (95% required). This includes the previously blocked process-inspection and localhost tests, plus available Docker-backed tests. Output is in `var/tmp/codereview-verification/quality-full-access.log`.
- Earlier restricted-session full regression run: **1,958 passed, 24 skipped, 3 failed in 653.42 seconds**, with **96.15% coverage**. One failure used a partial copied-plugin fixture incompatible with the stronger source identity. It was corrected to copy the real grading/contract/runtime tree without stubbing identity; **99 follow-up tests passed**. The two environment failures were blocked `ps` supervisor inspection and blocked localhost socket binding; both now pass with full access. No product assertion or gate was loosened.
- The frozen algorithmic product-source identity was verified unchanged across that full run. Afterward, the raw-fingerprint audit required the three shared-file companion releases above. These are release/reference metadata changes, not new bug-fix algorithms; **324 affected component/schema/CLI tests passed, 2 Docker-only tests skipped** after those updates. Three additional skills/diff/wire-contract projection tests also passed after their fixture metadata was updated.
- The earlier restricted-session `make quality` passed all static gates but stopped at the blocked-`ps` test with **576 passed, 16 skipped, 1 failed in 98.93 seconds** (`var/tmp/codereview-verification/quality-final.log`). The full-access run above supersedes that result and passes structure, smoke command shapes, all four lock freshness checks, ledger, Ruff formatting/lint, fan-out, Python quality, 16 import contracts, Lizard, mypy and pytest coverage.

- The precise root-file allowlist now permits the explicitly requested `codereview.md`; structure checks pass.
- Restricted-session UI checks: fixture projection and production TypeScript/Vite build passed. `make review-surface-test` could not install Chromium because the sandbox denied the browser cache's `__dirlock` directory. A direct Playwright run independently failed before browser scenarios because the sandbox denied the `127.0.0.1:5950` listener (`EPERM`).
- Restricted-session target/performance checks: `make target-status TARGET=mbp` reported the target unreachable, and `make perf-gate TARGET=mbp` could not start because the transport's PID file was outside the writable area. Accepted budgets were not changed.
- Dedicated browser and performance gates have not been rerun since the permission change; the latest `make quality` does include successful Docker-backed tests. Canonical archive-tag import remains optional pending user action. Skill corrections are applied, and no test gates were relaxed.
