"""Scenario-oriented application services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from raidar.application.models import (
    ScenarioCloneRequest,
    ScenarioInitRequest,
    ScenarioInitResult,
    ScenarioTemplateId,
    ScenarioValidationResult,
)
from raidar.application.scenario_catalog import load_scenario, save_scenario
from raidar.harness import harness_rule_filenames
from raidar.runtime.environments import resolve_environment_registry, resolve_scenario_environment
from raidar.runtime.profile import default_runtime_profile
from raidar.scenario_clone import ScenarioCloneResult
from raidar.scenario_clone import clone_scenario_revision as clone_revision
from raidar.schemas.environment import (
    CapabilityRequirements,
    EnvironmentConfig,
    EnvironmentResourcesConfig,
)
from raidar.schemas.scenario import (
    DeterministicCheck,
    PromptConfig,
    RequirementsConfig,
    RequirementSpec,
    ScenarioDefinition,
    ScenarioScorerRef,
    StarterConfig,
    VerificationConfig,
    VerificationGate,
)

REPO_ROOT = Path(__file__).resolve().parents[4]

SCENARIO_PROMPT_TEXT = (
    "Implement the requested feature in the starter application.\n\n"
    "Run all required verification commands before completion and "
    "report only after they pass.\n"
)
SCENARIO_RULE_TEXT = (
    "Follow the scenario prompt exactly. Run required verification commands before completion."
)


@dataclass(frozen=True)
class ScenarioInitLayout:
    scenario_root: Path
    scenario_name: str
    revision_dir: Path
    scenario_yaml: Path
    prompt_path: Path
    rules_dir: Path


@dataclass(frozen=True, slots=True)
class ScenarioTemplate:
    """Explicit environment, command, and scorer choices for a new scenario."""

    id: ScenarioTemplateId
    environment_id: str
    requirements: CapabilityRequirements
    setup_actions: tuple[tuple[str, ...], ...]
    verification_gates: tuple[tuple[str, tuple[str, ...]], ...]
    scorer_id: str


SCENARIO_TEMPLATES: dict[ScenarioTemplateId, ScenarioTemplate] = {
    "typescript-bun": ScenarioTemplate(
        id="typescript-bun",
        environment_id="node:20",
        requirements=CapabilityRequirements(
            runtimes={"node": ">=20"},
            package_managers={"bun": ">=1"},
            tools={"typescript": ">=5"},
        ),
        setup_actions=(("bun", "install", "--frozen-lockfile"),),
        verification_gates=(
            ("typecheck", ("bun", "run", "typecheck")),
            ("lint", ("bun", "run", "lint")),
        ),
        scorer_id="typescript-code-task",
    ),
    "python-pytest": ScenarioTemplate(
        id="python-pytest",
        environment_id="python:3.12",
        requirements=CapabilityRequirements(
            runtimes={"python": ">=3.12"},
            package_managers={"pip": ">=24"},
            tools={"coverage": ">=7", "pytest": ">=9", "ruff": ">=0.14"},
        ),
        setup_actions=(("python", "-m", "pip", "install", "-e", "."),),
        verification_gates=(
            ("test", ("python", "-m", "pytest")),
            ("lint", ("python", "-m", "ruff", "check", ".")),
        ),
        scorer_id="python-code-task",
    ),
}


def init_scenario(request: ScenarioInitRequest) -> ScenarioInitResult:
    """Create a new versioned scenario descriptor with prompt artifacts and rules."""

    layout = _scenario_init_layout(request)
    _ensure_new_scenario(layout)
    _create_scenario_init_dirs(layout)
    scenario = _build_scenario_definition(request, layout)
    save_scenario(layout.scenario_yaml, scenario)
    _write_scenario_prompt(layout)
    _write_scenario_rules(layout)
    return _scenario_init_result(request, layout)


def _scenario_init_layout(request: ScenarioInitRequest) -> ScenarioInitLayout:
    scenario_root = request.path.resolve()
    revision_dir = scenario_root / request.scenario_revision
    return ScenarioInitLayout(
        scenario_root=scenario_root,
        scenario_name=request.name or scenario_root.name,
        revision_dir=revision_dir,
        scenario_yaml=revision_dir / "scenario.yaml",
        prompt_path=revision_dir / request.prompt_entry,
        rules_dir=revision_dir / "rules",
    )


def _ensure_new_scenario(layout: ScenarioInitLayout) -> None:
    if layout.scenario_yaml.exists():
        raise FileExistsError(f"Scenario already exists: {layout.scenario_yaml}")


def _create_scenario_init_dirs(layout: ScenarioInitLayout) -> None:
    layout.rules_dir.mkdir(parents=True, exist_ok=True)
    (layout.revision_dir / "prompt").mkdir(parents=True, exist_ok=True)


def _build_scenario_definition(
    request: ScenarioInitRequest, layout: ScenarioInitLayout
) -> ScenarioDefinition:
    template = SCENARIO_TEMPLATES[request.template]
    gates = [
        VerificationGate(name=name, command=list(command))
        for name, command in template.verification_gates
    ]
    commands = [gate.command for gate in gates]
    return ScenarioDefinition(
        name=layout.scenario_name,
        display_name=request.display_name,
        scenario_revision=request.scenario_revision,
        parent_revision=None,
        description=f"Scenario definition for {layout.scenario_name}",
        difficulty=request.difficulty,
        task_kind=request.task_kind,
        delivery_mode=request.delivery_mode,
        workspace_state=request.workspace_state,
        starting_conditions=list(request.starting_conditions),
        languages=list(request.languages),
        evaluation_role=request.evaluation_role,
        catalog_status=request.catalog_status,
        family_id=request.family_id,
        variant_axes=dict(request.variant_axes),
        timeout_sec=request.timeout_sec,
        environment=EnvironmentConfig(
            kind="stack_preset",
            id=template.environment_id,
            workdir="/app",
            requirements=template.requirements.model_copy(deep=True),
            resources=EnvironmentResourcesConfig(),
            allow_internet=True,
        ),
        starter=StarterConfig(root=request.starter_root),
        verification=VerificationConfig(
            max_gate_failures=3,
            coverage_threshold=0.8,
            min_quality_score=0.8,
            setup_actions=[list(command) for command in template.setup_actions],
            required_commands=commands,
            gates=gates,
        ),
        requirements=RequirementsConfig(
            items=[
                RequirementSpec(
                    id="req-no-todo",
                    description="No TODO markers remain in production files.",
                    check=DeterministicCheck(
                        type="no_pattern",
                        pattern="TODO",
                        description="No TODO markers remain in production files",
                    ),
                )
            ]
        ),
        scorers=[
            ScenarioScorerRef(id=template.scorer_id, version=1, weight=0.9),
            ScenarioScorerRef(id="resource-efficiency", version=1, weight=0.1),
        ],
        prompt=PromptConfig(entry=request.prompt_entry),
    )


def _write_scenario_prompt(layout: ScenarioInitLayout) -> None:
    layout.prompt_path.parent.mkdir(parents=True, exist_ok=True)
    layout.prompt_path.write_text(SCENARIO_PROMPT_TEXT, encoding="utf-8")


def _write_scenario_rules(layout: ScenarioInitLayout) -> None:
    for filename in harness_rule_filenames():
        (layout.rules_dir / filename).write_text(SCENARIO_RULE_TEXT + "\n", encoding="utf-8")


def _scenario_init_result(
    request: ScenarioInitRequest, layout: ScenarioInitLayout
) -> ScenarioInitResult:
    return ScenarioInitResult(
        scenario_root=layout.scenario_root,
        scenario_name=layout.scenario_name,
        display_name=request.display_name,
        scenario_revision=request.scenario_revision,
        parent_revision=None,
        revision_dir=layout.revision_dir,
        scenario_yaml=layout.scenario_yaml,
        prompt_path=layout.prompt_path,
        rules_dir=layout.rules_dir,
        starter_root=request.starter_root,
        template=request.template,
        task_kind=request.task_kind,
        delivery_mode=request.delivery_mode,
        workspace_state=request.workspace_state,
        starting_conditions=list(request.starting_conditions),
        languages=list(request.languages),
        evaluation_role=request.evaluation_role,
        catalog_status=request.catalog_status,
        family_id=request.family_id,
        variant_axes=dict(request.variant_axes),
    )


def validate_scenario(path: Path) -> ScenarioValidationResult:
    """Validate a scenario document and return the typed result."""

    scenario_yaml = resolve_scenario_yaml(path)
    scenario = load_scenario(scenario_yaml)
    _validate_scenario_files(scenario_yaml.parent, scenario)
    runtime_profile = default_runtime_profile()
    resolve_scenario_environment(
        scenario=scenario,
        scenario_path=scenario_yaml,
        repo_root=REPO_ROOT,
        registry=resolve_environment_registry(
            repo_root=REPO_ROOT,
            runtime_profile=runtime_profile,
        ),
        docker_registry_allowlist=runtime_profile.docker_registry_allowlist,
    )
    return ScenarioValidationResult(
        scenario_path=scenario_yaml,
        scenario=scenario,
    )


def _validate_scenario_files(scenario_dir: Path, scenario) -> None:
    del scenario_dir
    scenario.resolved_metrics()


def clone_scenario_revision(request: ScenarioCloneRequest) -> ScenarioCloneResult:
    """Clone a scenario revision through the canonical service boundary."""

    return clone_revision(
        scenario_root=request.path.resolve(),
        source_revision=request.from_revision,
        target_revision=request.to_revision,
    )


def resolve_scenario_yaml(path: Path) -> Path:
    """Resolve a scenario root or revision directory to its scenario.yaml."""

    resolved = path.resolve()
    if resolved.is_file():
        return resolved
    scenario_yaml = resolved / "scenario.yaml"
    if scenario_yaml.is_file():
        return scenario_yaml
    candidates = list(resolved.glob("v*/scenario.yaml"))
    if not candidates:
        raise FileNotFoundError(f"scenario.yaml not found in {resolved}")
    return max(candidates, key=scenario_revision_sort_key)


def scenario_revision_sort_key(scenario_yaml: Path) -> tuple[int, str]:
    """Sort scenario.yaml paths by numeric revision directory when possible."""

    revision_dir = scenario_yaml.parent.name
    if not revision_dir.startswith("v") or not revision_dir[1:].isdigit():
        return (-1, revision_dir)
    return (int(revision_dir[1:]), revision_dir)


def scenario_revision_paths(scenario_root: Path) -> list[Path]:
    """Return scenario revisions sorted by canonical revision ordering."""

    if not scenario_root.is_dir():
        return []
    return sorted(scenario_root.glob("v*/scenario.yaml"), key=scenario_revision_sort_key)
