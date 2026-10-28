# Decision: how the grader provides packages that a submission declares

## Activity Summary
- **Outcome:** I recommend option **(d)**, a single resolver policy called `declared-wheels/1`. It contains option (a) and adds grading-time resolution, which is always on rather than opt-in. I reject (b) and (c).
- **Work done:**
  - Read `split-design.md` §1, §3, §5.1 and §6, the grading, seeding and network code, the `fb33b02` diff, and ledger fault FCR-004.
  - Scanned SCB at the pin `38d627ecf668`. The `scb-problems` checkout's problem folders match the pin; the files that differ at `ef6a9dd` are `README.md`, `conversion.toml` and files under `scripts/`.
  - Ran `uv pip compile` (uv 0.10.0) against live PyPI for every reference set.
- **Not done:** I edited no repository file and ran no Docker or trials. No claim below has been proved in a real grader.

## 0. Fact check (the brief's numbers corrected)
- **The brief overstates what is pre-installed.** Only PyYAML is provided to the submission:
  - `SUBMISSION_DEPENDENCY_PINS = {"pyyaml": …}` [OBSERVED `orchestrator/src/raidar/grading/submission_deps.py:34`];
  - `provisioned_pins` keeps only `test_dependencies` that are in that map [OBSERVED `submission_deps.py:62-72`].
  - Every other `test_dependency` goes into the grader's test venv, unpinned, and never reaches the graded program [OBSERVED `grading/grader_image.py:17-21,45-55`; `grading/bundle.py:233-240`].
- **The scan matches the brief:** 36 problems; 23 declare requirements; 20 declare packages outside `test_dependencies`; 16 need runtime packages outside them [OBSERVED: my scan of `*/solutions/checkpoint_*/requirements.txt` against `*/config.yaml`].
- **Under the rule the code actually applies, 18 problems are hit, not 16.** The extra two are:
  - `file_merger` (`pyarrow==16.1.0`) and `sheeteval` (`openpyxl`). Both are in `test_dependencies` but neither is pinned [OBSERVED: scan].
  - Separately, `eve_route_planner`, `rejector` and `metric_transform_lang` declare `pyyaml`, but it is not in their `test_dependencies`, so even PyYAML is unprovided for them [OBSERVED: scan].
  - So option (c) covers **at most 18 of 36** problems, not about 20.
- **Validation:** a step whose solvable set is empty is `excluded`, and any failing check excludes the problem [OBSERVED `design/problem/validation.py:9-12,27-29`]. A library imported lazily would silently shrink the solvable set instead of excluding the step [INFERENCE].
- **SCB native grading:**
  - runs `uv init` then `uv add -r requirements.txt` [OBSERVED `slop-code-bench/configs/environments/docker-python3.12-uv.yaml:12-15`], in the evaluation context [OBSERVED `src/slop_code/execution/models.py:243-244`];
  - the network defaults to `bridge` [OBSERVED `src/slop_code/execution/docker_runtime/models.py:97-100`];
  - it is unlocked, uses the latest version at grade time, and builds sdists [INFERENCE from the config].
  - The converter's `solve.sh` runs `pip install -r` [OBSERVED `scb-problems@38d627e:scripts/scb_to_harbor_lib/renderers.py:212-214`].
- **Raidar's grader today:**
  - `--network none` [OBSERVED `grading/sandbox.py:230-231`]; `UV_OFFLINE` and `UV_NO_SYNC` [OBSERVED `sandbox.py:177,183`]; extra entries go on `PYTHONPATH` [OBSERVED `sandbox.py:173`].
  - `unprovided` is stored in `StepRecord.submission_dependencies` [OBSERVED `grading/records.py:93`], but nothing in `engine/`, `evaluation/` or `scoring/` reads it [OBSERVED: grep]. There is no health reading today.
- **The agent sandbox:**
  - can reach `pypi.org` and `files.pythonhosted.org` [OBSERVED `engine/trial/network.py:24-26`];
  - is seeded with unpinned `test_dependencies` and a `uv sync` [OBSERVED `engine/trial/workspace.py:38-65`, `engine/trial/run.py:61`];
  - never has its `.venv` included in the graded snapshot [OBSERVED `contract/snapshot.py:22`].
- **Fault IDs:** the brief's FCR-007 is ledger fault **FCR-004** [OBSERVED `lab/ledger/faults/FCR-004.md:31`]. The fix criterion it records accepts either an offline wheel cache or a networked install, and leaves the choice to the owner [OBSERVED `FCR-004.md:20-22`].
- **Platform wheels are not a blocker.** All 23 declared reference sets resolve wheels-only for cp312 on both `x86_64-manylinux_2_28` and `aarch64-manylinux_2_28`. The version sets are identical across the two architectures (cutoff 2026-10-01). A per-architecture hashed `pylock.toml` for `execution_server` is 32 packages and about 12.5 KB [OBSERVED: `uv pip compile` output, 2026-10-27].

## 1. Recommendation
**K1. Adopt (d), `declared-wheels/1`: one policy for every graded snapshot, whether a reference or an agent's.**
- **Resolve** the declared requirements with uv, under these limits:
  - wheels only (`--only-binary :all:`);
  - Python 3.12, for the pack's Linux architecture;
  - index `pypi.org` only;
  - an `exclude-newer` cutoff fixed in the problem pin.
- **Install** the hash-checked wheels into a grader image layer. That layer is built with network, but no package code runs during the install.
- **Grade offline**, exactly as today.
- References are resolved at import and committed. Agent sets are resolved when grading and stored in the verifier record.

Rationale:
- **Fidelity.** It matches SCB native for every package that has a wheel released before the cutoff. It differs only for packages that ship only as sdists, for releases after the cutoff, and from native's "latest at grade time".
- **Fairness.** It does not care which library an arm picks. Option (a) rewards arms that guess the reference's stack. For example, an `etl_pipeline` agent using polars or sqlparse would fail, while duckdb plus sqlglot would pass [INFERENCE]. The ledger calls FCR-004 "symmetric across arms" [OBSERVED `FCR-004.md:14-16`], but that was PyYAML on forge. Library choice varies by workflow, so the general case is bias [INFERENCE].
- **Security.** Agent-controlled code never runs with network:
  - resolution reads metadata only;
  - installing a wheel runs no code [OBSERVED `split-design.md:206`];
  - sdists are never built;
  - hidden tests only ever enter the `--network none` container [OBSERVED `sandbox.py:3-7`].
- **Determinism.** The cutoff, the hashes and the recorded pylock fix the versions. Regrading reinstalls the recorded lock and never re-resolves.
- **Need 1 coverage.** All 23 reference sets resolve, so every problem becomes provisionable [INFERENCE until validated in Docker].
- **Fit with planned work.** This is the same machinery as phase 4's `install/1` ("wheels installed offline and hash-checked") and its ST5 lock check, so it is built once [OBSERVED `split-design.md:196,402,573,601`].

**K2. Why not "(a) plus an opt-in agent install".** An opt-in splits identities between experiments, and arms run with and without it can't be pooled. (a) is just (d) limited to references. Making (d) the pin's policy costs only the grading-time resolve step.

**K3. Agent sandbox.** Don't seed or cache the reference set. It reveals the reference's architecture (`code_search` → tree-sitter; `etl_pipeline` → duckdb and sqlglot; `datagate` → flask and pandas), and SCB-native agents don't get it either [INFERENCE]. Instead, make installs during the agent's work follow the same rules as grading (3.3).

## 2. Options
| | (a) pin references at import | (b) networked install, then offline grading | (c) accept the gap | **(d) declared-wheels/1** |
|---|---|---|---|---|
| Fidelity to SCB native | Reference libraries only | Highest | Lowest | Any wheel released before the cutoff |
| Fairness | Favours arms that pick the reference's libraries | Neutral | Penalises library users | Neutral, except sdist-only and post-cutoff packages |
| Determinism | High | Drifts unless locked; sdist builds | High | High: cutoff, hashes and a recorded lock |
| Security | Strong | Weak: sdist builds run agent-chosen code with network | Strong | Strong: no build, offline grading |
| Need 1 coverage | 36/36 [INFERENCE] | 36/36 | ≤18/36 | 36/36 [INFERENCE] |
| Effort | M | M, plus a security review | None | M+ (a's work plus grading-time resolution; shares phase 4) |

## 3. Design
**3.1 At import (`make problem-import`)**
1. For each reference step, read the declared set with the existing `declared_requirements` [OBSERVED `submission_deps.py:110-117`]. Identical sets are stored once, keyed by digest.
2. Resolve each set with: `uv pip compile --format pylock.toml --generate-hashes --only-binary :all: --python-version 3.12 --python-platform {x86_64,aarch64}-manylinux_2_28 --exclude-newer <cutoff> --index-url https://pypi.org/simple`.
3. Write the results to `lab/problems/registry/scb/<problem>/<version>.deps/pylock.<set12>-{amd64,arm64}.toml`. These are generated, committed records, like the other pins [OBSERVED `split-design.md:128`].
4. If a set doesn't resolve, or the two architectures resolve to different versions, mark the problem `excluded` with reason `dependency_unresolvable`.
5. Add a `dependencies` block to the import manifest (for example `.../log_query/38d627ecf668.json`):
   ```json
   "dependencies": {"policy": "declared-wheels/1", "resolver": "uv@0.10.0", "index": "https://pypi.org/simple",
     "python": "3.12", "platforms": ["x86_64-manylinux_2_28", "aarch64-manylinux_2_28"],
     "exclude_newer": "<import time, UTC>",
     "sets": {"<set sha256>": {"requirements": ["flask>=3.0.0", "..."], "amd64": "<pylock sha256>", "arm64": "<pylock sha256>"}},
     "steps": {"checkpoint_1": "<set sha256>"}}
   ```
6. Validation then grades references with their sets installed. A reference that still has anything `unprovided` fails the `solvable_set` check.

**3.2 Grader (clean cutover)**
- **Delete** `SUBMISSION_DEPENDENCY_PINS`, `provisioned_pins`, the per-package directories and `dockerfile_layer` [OBSERVED `submission_deps.py:34,62-87`].
- **When grading a step:**
  1. Compute the declared set's digest.
  2. If the pin already has that set, use the committed pylock. Otherwise resolve it on the host under the pin's policy, with a timeout and caps.
  3. Mark refused requirements as `unprovided`: direct references (`pkg @ url`, paths), unparseable lines, and markers handled as now. Index-option lines are already dropped [OBSERVED `submission_deps.py:107`].
- **Install:** build an image `FROM <problem grader image>` with one layer: `RUN uv pip install --require-hashes --no-deps --only-binary :all: --target /opt/raidar-submission-deps/<lock12> -r <pylock>`.
  - The tag is the digest of the base tag plus the pylock.
  - The build runs on the Docker daemon with network, holds no tests, and runs no package code.
- **Expose** the whole closure on `PYTHONPATH`. Native `uv add` installs the closure too, so transitive imports work there as well.
  - Process `.pth` files through `site.addsitedir` in a sitecustomize in the runtime directory [INFERENCE: needs a test].
  - An empty declared set uses the base grader image unchanged.
- **Resolver or PyPI unreachable:** this is an infrastructure failure, not `unprovided`. Re-resolving a committed reference set acts as the canary (§5.3 rule).

**3.3 Agent sandbox**
- The D2 seed keeps `test_dependencies` [OBSERVED `split-design.md:101-103`], resolved under the same policy.
- The seeded `pyproject.toml` gains `[tool.uv] exclude-newer = "<cutoff>"` and `no-build = true`. The agent user's `uv.toml` gets the same two keys, plus `PIP_ONLY_BINARY=:all:` [INFERENCE: pip's environment-variable mapping]. Then a `uv add` during work fails exactly where grading would.
- No reference set enters the agent image, the workspace or a cache.

**3.4 Identity**
- **Problem pin:** the `dependencies` block is part of the manifest, so a change to it forces revalidation through RT0.
- **Experiment lock:** add `dependencies_sha256` per problem, beside `bundle_sha256` [OBSERVED `lab/experiments/smoke/python__onboarding__need-one/experiment.lock.json:378`].
- **Policy versioning:** the policy name is versioned. A change to resolver behaviour bumps it, and every experiment is relocked.
- **Trial evidence:** extend `StepRecord.submission_dependencies` with:
  - `declared`, `set_sha256`, and `source` (pin or resolved);
  - the pylock text and its sha256;
  - `arch`, `provided`, and `unprovided` as `[{requirement, reason}]`;
  - `resolve_s`, `build_s` and the grader image ID.
- **Pooling across architectures:** allowed only where the versions are equal. Agent sets are resolved for both architectures; a mismatch records the reading `arch_divergent`.

**3.5 Health**
- **Readings per step** (readings never invalidate a trial [OBSERVED `split-design.md:474`]):
  - `submission_deps.unprovided`, with a reason: `no-wheel`, `after-cutoff`, `unresolvable`, `direct-reference`, `unparseable` or `cap-exceeded`;
  - `undeclared_import`: a `ModuleNotFoundError` in `sandbox.log` for a non-stdlib module the snapshot didn't declare. This is FCR-004's shape, and native grading fails it too.
- **Validity:** a resolver that is unavailable makes the V row `unmeasured`. The trial is treated as an infrastructure failure and excluded under the cap; it is not counted as an agent outcome.
- **Per arm:**
  - the share of steps with at least one `unprovided` package;
  - the share of steps with `undeclared_import`;
  - the solvable cases failed in those steps.

  Analysis prints this beside the primary outcome. It warns when arms differ materially, mirroring the exclusion rule [OBSERVED `split-design.md:489-491`].
- **Ledger:** close FCR-004's `solution` and `validation` fields once 4.3 below passes.

## 4. Risks and tests
**Risks**
- **PyPI is needed at grading time.** Mitigate with a host wheel cache under `var/`. Regrades use the recorded lock.
- **Yanked or deleted files** could break rebuilding old locks [INFERENCE]. Keep the wheel cache; the image ID is already recorded.
- **Cold image builds per declared set.** The N1 budget treats cold builds as record-only [OBSERVED `grader_image.py:17-18`]. Cache by lock digest and measure in `make perf-gate`.
- **`.pth` files and namespace packages** may not work in `--target` directories (`protobuf` in `mocked_http`) [INFERENCE].
- **Huge or hostile closures** (DoS). Caps on package count and bytes are needed.
- **A malicious wheel** imported at grading time has the same trust as the submission's own code. It is contained by `--network none` and dropped capabilities [OBSERVED `sandbox.py:230-240`].
- **Version gap from native** "latest at grade time", which is a fidelity limit.

**Tests**
1. **Resolver:**
   - the same input gives a byte-identical pylock;
   - an sdist-only package gives `no-wheel`;
   - a specifier after the cutoff gives `after-cutoff`;
   - `pkg @ https://…` gives `direct-reference`;
   - an architecture mismatch is detected.
2. **Security:**
   - a planted sdist whose `setup.py` writes a marker or opens a socket is never executed;
   - the grader still runs `--network none`;
   - the build context holds no tests.
3. **Docker validation, `REPEATS=3`:** `datagate`, `execution_server`, `code_search` (binary tree-sitter), `mocked_http` and `metric_transform_lang` all end with non-empty solvable sets.
4. **Fairness:** planted `datagate` snapshots using pandas and using polars both end `provided`.
5. **Outage:** regrade with the cache wiped and PyPI blocked. The result must be an infrastructure failure, not a score of 0.
6. **Native parity** on the affected problems. This is blocked on `mbp` until phase 9 [OBSERVED `split-design.md:578`].

## 5. Open questions for the owner
1. **Cutoff:** import time (proposed), the SCB pin's commit date, or the experiment's lock time?
2. **Sdists:** refuse them (proposed), or build them in an isolated container with no secrets?
3. **Caps:** what package-count and byte limits for a closure?
4. **Grader test venv:** should its `test_dependencies` be pinned under the same policy? Today they are unpinned, which is a determinism gap [OBSERVED `grader_image.py:17-21`].
5. **Analysis threshold:** what counts as a "material" difference in the `unprovided` rate between arms?
6. **Work-time limits:** do you accept the cutoff and no-build limits on agents' own installs? SCB-native agents have neither.
7. **Host network:** is host PyPI access during grading acceptable on `mbp`?
8. **`[dependency-groups]`:** should they be read? Native `uv run` syncs the default groups; Raidar does not [OBSERVED `sandbox.py:178-183`].
