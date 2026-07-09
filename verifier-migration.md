# Verifier Migration Plan

## Objective

Make scorer definitions the canonical owner of verification intent, evidence collection requirements, and score interpretation. Use one runtime evidence runner only to execute scorer-declared actions inside the task workspace and return raw evidence for Python score synthesis.

## Target Architecture

The final model has four responsibilities.

Scenario contracts describe task inputs, authored requirements, starter material, and scenario-specific parameters such as visual references, thresholds, required artifacts, and setup parameters. They do not own scorer command plans.

Environment presets describe available capabilities: runtimes, package managers, tools, browsers, resources, and workspace policy.

Scorers define metrics, capability requirements, verification actions, evidence expectations, and score interpretation.

The Python evidence runner executes resolved actions inside the Harbor task image and writes raw evidence. It does not compute quality scores, scorer results, or scorecard policy.

## Canonical Flow

1. Load the scenario.
2. Resolve attached scorers.
3. Merge scorer capability requirements with scenario environment requirements.
4. Validate the selected environment satisfies the merged capability contract.
5. Ask each scorer for its verification plan.
6. Normalize all scorer plans into one ordered action plan.
7. Bundle the single Python evidence runner and the resolved action plan.
8. Run the evidence runner inside the task image after harness execution.
9. Persist raw evidence and captured artifacts.
10. Pass raw evidence to scorer implementations.
11. Build metric scores, scorer results, performance gates, and final scorecard in Python.

## Core Types

### VerificationAction

Represents one executable evidence step.

```python
@dataclass(frozen=True)
class VerificationAction:
    id: str
    kind: str
    timeout_sec: int | None = None
    required: bool = True
    depends_on: tuple[str, ...] = ()
    config: dict[str, Any] = field(default_factory=dict)
```

Initial action kinds:

- `command`: run an argv command and capture stdout, stderr, exit code, and duration.
- `read_file`: read a bounded text file.
- `read_json`: read a JSON file.
- `glob`: list files matching a workspace-relative pattern.
- `visual_diff`: compare reference and actual image paths and produce diff evidence.
- `archive`: copy or package a declared artifact path.

### VerificationPlan

Represents one scorer-owned evidence plan.

```python
@dataclass(frozen=True)
class VerificationPlan:
    owner: str
    actions: tuple[VerificationAction, ...]
```

### VerificationPlanContext

Represents the resolved context available when a scorer declares evidence needs.

```python
@dataclass(frozen=True)
class VerificationPlanContext:
    scenario: ScenarioContract
    resolved_scorer: ResolvedScorer
    scenario_dir: Path
    environment_capabilities: EnvironmentCapabilities
```

This keeps scorer planning tied to resolved metric configuration, scorer registry metadata, scenario paths, and selected environment capabilities. Scorers must not re-parse raw scenario scorer declarations to recover their own configuration.

### VerificationEvidence

Represents evidence returned by the runner.

```python
@dataclass(frozen=True)
class VerificationEvidence:
    actions: dict[str, dict[str, Any]]
    artifacts: dict[str, str]
    errors: list[dict[str, Any]]
```

### BaseScorer Contract

Scorers expose two runtime hooks.

```python
class BaseScorer:
    requirements: ClassVar[CapabilityRequirements]

    def verification_plan(self, context: VerificationPlanContext) -> VerificationPlan:
        return VerificationPlan(owner=self.id, actions=())

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        return ScorerEvidence()
```

`ScorerContext` receives `verification_evidence` as a first-class field.

## Example: Homepage Design-To-Code

The design-to-code scorer owns the browser and visual evidence it needs.

```python
class DesignToCode(BaseScorer):
    requirements = CapabilityRequirements(
        runtimes={"node": ">=20"},
        package_managers={"bun": ">=1"},
        tools={
            "typescript": ">=5",
            "playwright": ">=1",
            "odiff": ">=0",
        },
        browsers={"chromium": "installed"},
    )

    def verification_plan(self, context: VerificationPlanContext) -> VerificationPlan:
        visual = context.scenario.visual
        return VerificationPlan(
            owner=self.id,
            actions=(
                command("typecheck", ["bun", "run", "typecheck"]),
                command("lint", ["bun", "run", "lint"]),
                command("coverage", ["bun", "run", "test:coverage"]),
                command("build", ["bun", "run", "build"]),
                command("capture", visual.screenshot_command),
                visual_diff(
                    "visual.homepage",
                    reference=visual.reference_image,
                    actual=visual.artifact_manifest.actual_image,
                    diff=visual.artifact_manifest.diff_image,
                    regions=visual.regions,
                ),
                read_json("coverage.summary", "coverage/coverage-summary.json", required=False),
                glob("artifacts.components", "src/components/**/*.tsx"),
            ),
        )

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        evidence = context.verification_evidence
        visual = evidence.actions["visual.homepage"]
        coverage = evidence.actions.get("coverage.summary")
        return ScorerEvidence(
            metric_scores=(
                self.visual_regression_score(visual, context.scenario.visual),
                self.functional_score(evidence),
                self.coverage_score(coverage, evidence.actions["coverage"]),
                self.artifact_score(context.workspace),
                self.verification_stability_score(evidence),
            )
        )
```

## Plan Resolution Rules

The orchestrator resolves scorer plans before the task bundle is created.

1. Call `verification_plan(context)` for each attached scorer after scorer registry resolution and environment selection.
2. Prefix stored action IDs with the scorer owner, for example `design_to_code.typecheck`, while preserving owner-local IDs for scorer helper methods.
3. Validate `depends_on` references after namespacing and reject cycles before bundle creation.
4. De-duplicate actions only when their kind, config, timeout, required flag, and dependency set are identical. Store aliases so each scorer can still address its owner-local evidence ID.
5. Run workspace setup and environment preflight outside scorer evidence actions unless a scorer explicitly declares an action because it needs the result as evidence.
6. Treat required action failure as raw evidence. The runner records the failure; scorer code decides metric impact. The runner process fails only for invalid plans, infrastructure failures, or evidence-write failures.
7. Hash the normalized plan, capability contract, and runner contract into the effective run contract before cache and task-image decisions.

The scenario still owns scenario-specific visual parameters, but the scorer owns why those parameters matter and how they become score.

## Example: Python Code Task

The Python code-task scorer owns Python-specific evidence.

```python
class PythonCodeTask(CodeTaskScorer):
    requirements = CapabilityRequirements(
        runtimes={"python": ">=3.12"},
        tools={
            "pytest": ">=9",
            "coverage": ">=7",
            "ruff": ">=0.14",
            "lizard": ">=1.17",
        },
    )

    def verification_plan(self, context: VerificationPlanContext) -> VerificationPlan:
        return VerificationPlan(
            owner=self.id,
            actions=(
                command("compile", ["python", "-m", "compileall", "-q", "."]),
                command("pytest", ["python", "-m", "pytest", "-q"]),
                command(
                    "coverage.run",
                    ["python", "-m", "coverage", "run", "-m", "pytest", "-q"],
                    required=False,
                ),
                command(
                    "coverage.json",
                    ["python", "-m", "coverage", "json", "-o", ".raidar/coverage.json"],
                    required=False,
                ),
                read_json("coverage.report", ".raidar/coverage.json", required=False),
                command("ruff", ["python", "-m", "ruff", "check", "."]),
                command("lizard", ["python", "-m", "lizard", ".", "--CCN", "10", "--length", "100"]),
                glob("python.sources", "**/*.py"),
            ),
        )

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        evidence = context.verification_evidence
        return ScorerEvidence(
            metric_scores=(
                self.functional_score(evidence),
                self.code_quality_score(evidence.actions["ruff"], evidence.actions["lizard"]),
                self.test_coverage_score(evidence.actions.get("coverage.report")),
                self.artifact_score(context.workspace),
                self.verification_stability_score(evidence),
            )
        )
```

## Migration Phases

### Phase 1: Model The Canonical Contracts

- Add `VerificationAction`, `VerificationPlan`, and `VerificationEvidence`.
- Add `VerificationPlanContext` with resolved scorer configuration, scenario contract, scenario path, and selected environment capabilities.
- Add action helper constructors for `command`, `read_file`, `read_json`, `glob`, `visual_diff`, and `archive`.
- Extend `BaseScorer` with `verification_plan`.
- Extend `ScorerContext` with `verification_evidence`.
- Add schema validation for action IDs, argv commands, workspace-relative paths, and duplicate action ownership.
- Add plan normalization validation for namespacing, dependencies, de-duplication, aliases, and required action failure behavior.

Acceptance criteria:

- Unit tests cover action validation.
- Scorer definitions can expose plans without executing them.
- Scenario resolution can merge scorer requirements into the environment contract.
- Scorers can build plans from resolved metric configuration without reading raw scenario scorer declarations.

### Phase 2: Build The Single Python Evidence Runner

- Add one bundled Python evidence runner asset.
- Input: resolved `verification-plan.json`.
- Output: `evidence.json`, action logs, and declared artifact files.
- Implement bounded command execution with timeout, stdout/stderr capture, exit code, duration, and redaction.
- Implement file readers with size limits.
- Implement glob listing with excluded runtime directories.
- Implement visual diff action using configured tool commands.
- Ensure all outputs are stable JSON with no scorecard fields.

Acceptance criteria:

- Runner produces deterministic evidence for command, file, JSON, glob, and visual actions.
- Runner failures are represented as action errors, not scorer outputs.
- The runner never emits metric scores, scorer results, quality scores, or performance gates.

### Phase 3: Rewire Bundle Creation

- Generate one merged verification plan during task bundle creation.
- Bundle the evidence runner and `verification-plan.json`.
- Run the evidence runner from the Harbor test script.
- Persist `evidence.json` and action logs under verifier artifacts.
- Remove environment-level runner selection from scenario execution.
- Replace runner selection in the effective run contract with a stable evidence-runner contract version and the normalized verification-plan hash.
- Include scorer requirements, selected environment capabilities, and normalized plan hash in cache and task-image invalidation.

Acceptance criteria:

- The Harbor bundle always contains the same runner asset.
- Task images are invalidated by scorer plans, scorer requirements, environment capabilities, and runner contract.
- Cache identity changes when scorer-declared commands, files, visual comparisons, dependencies, or required flags change.
- Environment presets only declare capabilities and resources.

### Phase 4: Move Evidence Interpretation Into Scorers

- Update all scorer implementations to consume `verification_evidence`.
- Move functional, coverage, requirements, visual, artifact, and stability interpretation into scorer-owned methods.
- Keep shared helpers in scorer utility modules when multiple scorers need the same evidence parsing.
- Remove score interpretation from runtime action execution.

Acceptance criteria:

- Every selected metric is emitted by an attached scorer.
- Missing scorer evidence results in explicit metric failure with clear evidence.
- The scorecard phase only aggregates scorer outputs and applies run-level validity checks.

### Phase 5: Simplify Execution Outputs

- Replace verifier-produced scorecard parsing with raw evidence loading.
- Make execution outputs contain raw evidence, gate/action history, process metrics, and trace events.
- Build functional, visual, coverage, requirements, and stability metrics through scorer outputs.
- Keep execution validity as run-level orchestration policy.

Acceptance criteria:

- Scorecard synthesis does not depend on runtime-generated scorecard JSON.
- Canonical scorecard artifacts are always produced by Python score synthesis.
- `reward.txt` is written only from the canonical scorecard result.

### Phase 6: Clean The Public Config Surface

- Remove environment runner metadata from environment schemas and presets.
- Move scenario-authored command gates, setup actions that produce scoring evidence, test discovery globs, and required command lists into scorer-owned verification-plan helpers.
- Keep non-scoring setup and preflight as orchestration setup, not scorer evidence.
- Keep scenario verification config focused on thresholds, expected artifacts, fixture references, task constraints, and setup parameters consumed by scorers or orchestration setup.
- Keep scorer definitions as the source of tool requirements and evidence actions.
- Update scenario validation to fail when scorer-required capabilities are absent from the selected environment.

Acceptance criteria:

- Environment config answers "what is available?"
- Scorer config answers "what evidence do we need and how is it scored?"
- Scenario config answers "what is this task asking for?"
- No scenario command list remains as an independent scoring source.

### Phase 7: Prune Superseded Runtime Paths

- Remove non-canonical verifier assets.
- Remove multi-runner verifier registry code.
- Remove tests that assert runner selection by environment.
- Remove parser paths that expect runtime-generated scorecard files as canonical inputs.
- Remove output aliases that preserve non-canonical verifier terminology.

Acceptance criteria:

- Repository search shows only the single evidence runner contract.
- No environment preset selects a verifier implementation.
- No bundled script computes scorer results or scorecard fields.

### Phase 8: Validate With Representative Scenarios

- Validate homepage design-to-code.
- Validate Python code task.
- Validate a simple smoke scenario.
- Validate matrix dry-run output.
- Validate task-image cache identity changes when scorer plans or requirements change.
- Run root quality gates.

Acceptance criteria:

- `make scenario-validate` passes for representative scenarios.
- `make runtime-stack-scenario-smoke` passes for one Node scenario and one Python scenario.
- `make quality` passes.
- Persisted run artifacts contain raw evidence, canonical scorecard, action logs, and final workspace archive.

## Implementation Order

1. Add contracts and validation tests.
2. Add scorer `verification_plan` hooks with no runtime behavior change.
3. Add the evidence runner and direct unit tests.
4. Wire bundle creation to emit `verification-plan.json`.
5. Load `evidence.json` into execution results.
6. Convert Python code-task scorer to scorer-owned evidence.
7. Convert design-to-code scorer to scorer-owned evidence.
8. Convert remaining scorers.
9. Remove environment runner metadata.
10. Prune superseded runtime paths.
11. Run scenario smoke validation.
12. Run `make quality`.

## Design Rules

- Scorers own score meaning.
- The runner owns only execution mechanics.
- Environments declare available capabilities, not scoring behavior.
- Scenarios supply task-specific parameters, not scorer implementation details.
- Runtime evidence is raw, bounded, redacted, and reproducible.
- Canonical scorecards are produced once, in Python score synthesis.
- No scoring policy is embedded in bundled task-runtime scripts.

## Risks And Controls

- Risk: scorer plans duplicate common command actions.
  Control: provide shared scorer-plan helper functions and base scorer mixins.

- Risk: action IDs collide across scorers.
  Control: namespace action IDs by scorer owner during plan normalization.

- Risk: evidence runner grows into another scoring layer.
  Control: forbid metric fields in runner output schema.

- Risk: Node scenarios need Python for the runner.
  Control: treat Python as Raidar infrastructure in all managed task images. Node, browser, package-manager, and shell behavior stays inside scorer-declared command actions, not separate runner runtimes.

- Risk: visual scoring loses fidelity during migration.
  Control: snapshot current expected visual evidence payloads and require parity at the scorer-output level.

## Done Criteria

- One canonical evidence runner exists.
- Scorers declare verification plans.
- Scorers interpret all evidence into metric scores.
- Environment presets do not select verifier implementations.
- Runtime-generated evidence contains no scorecard policy.
- Canonical scorecards are synthesized by Python.
- Representative Node, Python, and smoke scenarios pass validation.
- `make quality` passes.
