"""Environment library resolution and scorer compatibility tests."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from raidar.application.scenario_catalog import load_scenario
from raidar.harness import HarnessDefinitionError, harness_definition
from raidar.harness import definitions as harness_definitions
from raidar.runtime.environments import (
    EnvironmentResolutionError,
    capability_spec_satisfies,
    combined_capability_requirements,
    environment_library_index,
    normalize_probe_version,
    resolve_environment_registry,
    resolve_scenario_environment,
)
from raidar.runtime.profile import RuntimeProfile
from raidar.runtime.tool_catalog import (
    ToolCatalogError,
    _load_tool_catalog,
    installed_probe_value,
    probe_command,
    tool_catalog_payload,
)
from raidar.schemas.scenario import ScenarioDefinition


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _runtime_profile(*, registry_roots: tuple[str, ...] = ("environments",)) -> RuntimeProfile:
    return RuntimeProfile(environment_registry_roots=registry_roots)


def _resolve_environment(scenario, scenario_path: Path, repo_root: Path):
    runtime_profile = _runtime_profile()
    return resolve_scenario_environment(
        scenario=scenario,
        scenario_path=scenario_path,
        repo_root=repo_root,
        registry=resolve_environment_registry(
            repo_root=repo_root,
            runtime_profile=runtime_profile,
        ),
        docker_registry_allowlist=runtime_profile.docker_registry_allowlist,
    )


def _write_environment_metadata(
    root: Path,
    relative_dir: str,
    *,
    environment_id: str,
    registry_root: str = "environments",
) -> None:
    environment_dir = root / registry_root / relative_dir
    environment_dir.mkdir(parents=True)
    (environment_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (environment_dir / "environment.yaml").write_text(
        f"""id: {environment_id}
version: 1
image: raidar/test:latest
build:
  dockerfile: Dockerfile
capabilities:
  runtimes:
    python: ">=3.12"
  package_managers:
    pip: ">=24"
  tools:
    git: ">=2"
    pytest: ">=9"
    coverage: ">=7"
    ruff: ">=0.14"
    lizard: ">=1.17"
  browsers: {{}}
""",
        encoding="utf-8",
    )


def test_resolve_python_scenario_environment_from_library() -> None:
    scenario_path = _repo_root() / "scenarios/python-code-task-baseline/v001/scenario.yaml"
    scenario = load_scenario(scenario_path)

    environment = _resolve_environment(scenario, scenario_path, _repo_root())

    assert environment.id == "python:3.12"
    assert environment.config.kind == "stack_preset"
    assert environment.library.capabilities.runtimes["python"] == ">=3.12"
    assert environment.library.capabilities.tools["ruff"] == ">=0.14"
    assert environment.dockerfile_path == _repo_root() / "environments/python/3.12/Dockerfile"


def test_node_environment_provides_the_generic_evidence_runner() -> None:
    scenario_path = _repo_root() / "scenarios/hello-world-smoke/v001/scenario.yaml"
    scenario = load_scenario(scenario_path)

    environment = _resolve_environment(scenario, scenario_path, _repo_root())

    assert environment.library.capabilities.runtimes["python"] == ">=3.11"
    assert "python-is-python3" in environment.dockerfile_path.read_text(encoding="utf-8")


def test_environment_config_requires_custom_docker_metadata() -> None:
    scenario_path = _repo_root() / "scenarios/python-code-task-baseline/v001/scenario.yaml"
    payload = load_scenario(scenario_path).model_dump(mode="json")
    payload["environment"] = {
        "kind": "custom_docker",
        "id": "local-python",
        "workdir": "/app",
        "requirements": {"runtimes": {"python": ">=3.12"}},
        "resources": {
            "cpus": 2,
            "memory_mb": 4096,
            "storage_mb": 10240,
        },
        "allow_internet": True,
    }
    with pytest.raises(ValueError, match="custom_docker environments require"):
        ScenarioDefinition.model_validate(payload)


def test_environment_resolution_supports_custom_docker() -> None:
    scenario_path = _repo_root() / "scenarios/python-code-task-baseline/v001/scenario.yaml"
    payload = load_scenario(scenario_path).model_dump(mode="json")
    payload["environment"] = {
        "kind": "custom_docker",
        "id": "local-python",
        "image": "raidar/local-python:latest",
        "build": {"dockerfile": "environments/python/3.12/Dockerfile"},
        "capabilities": {
            "runtimes": {"python": ">=3.12"},
            "package_managers": {"pip": ">=24", "uv": ">=0.8"},
            "tools": {
                "git": ">=2",
                "pytest": ">=9",
                "coverage": ">=7",
                "ruff": ">=0.14",
                "lizard": ">=1.17",
            },
            "browsers": {},
        },
        "workdir": "/app",
        "requirements": {"runtimes": {"python": ">=3.12"}},
        "resources": {
            "cpus": 2,
            "memory_mb": 4096,
            "storage_mb": 10240,
        },
        "allow_internet": True,
    }
    scenario = ScenarioDefinition.model_validate(payload)

    environment = _resolve_environment(scenario, scenario_path, _repo_root())

    assert environment.id == "local-python"
    assert environment.config.kind == "custom_docker"
    assert environment.dockerfile_path == _repo_root() / "environments/python/3.12/Dockerfile"


def test_environment_resolution_rejects_missing_scorer_inventory() -> None:
    scenario_path = _repo_root() / "scenarios/python-code-task-baseline/v001/scenario.yaml"
    payload = load_scenario(scenario_path).model_dump(mode="json")
    payload["environment"] = {
        "kind": "stack_preset",
        "id": "node:20",
        "workdir": "/app",
        "requirements": {"runtimes": {"node": ">=20"}},
        "resources": {
            "cpus": 2,
            "memory_mb": 4096,
            "storage_mb": 10240,
        },
        "allow_internet": True,
    }
    scenario = ScenarioDefinition.model_validate(payload)

    with pytest.raises(EnvironmentResolutionError, match="scorer python-code-task@1"):
        _resolve_environment(scenario, scenario_path, _repo_root())


def test_environment_library_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(EnvironmentResolutionError, match="Environment library not found"):
        resolve_environment_registry(repo_root=tmp_path, runtime_profile=_runtime_profile())


def test_environment_library_rejects_duplicate_ids(tmp_path: Path) -> None:
    _write_environment_metadata(tmp_path, "python/a", environment_id="python:3.12")
    _write_environment_metadata(tmp_path, "python/b", environment_id="python:3.12")

    with pytest.raises(EnvironmentResolutionError, match="Duplicate environment id"):
        resolve_environment_registry(repo_root=tmp_path, runtime_profile=_runtime_profile())


def test_environment_registry_preserves_root_order_and_fingerprints_entries(
    tmp_path: Path,
) -> None:
    _write_environment_metadata(
        tmp_path,
        "python/a",
        environment_id="python:a",
        registry_root="registry-a",
    )
    _write_environment_metadata(
        tmp_path,
        "python/b",
        environment_id="python:b",
        registry_root="registry-b",
    )
    runtime_profile = _runtime_profile(registry_roots=("registry-b", "registry-a"))

    registry = resolve_environment_registry(
        repo_root=tmp_path,
        runtime_profile=runtime_profile,
    )

    assert registry.roots == (tmp_path / "registry-b", tmp_path / "registry-a")
    assert tuple(environment_library_index(registry)) == ("python:b", "python:a")
    payload = registry.cache_payload()
    assert [entry["id"] for entry in payload["entries"]] == ["python:b", "python:a"]
    assert all(len(entry["metadata_fingerprint"]) == 64 for entry in payload["entries"])
    assert all(len(entry["dockerfile_fingerprint"]) == 64 for entry in payload["entries"])


def test_environment_registry_rejects_duplicate_ids_across_roots(tmp_path: Path) -> None:
    _write_environment_metadata(
        tmp_path,
        "python/a",
        environment_id="python:duplicate",
        registry_root="registry-a",
    )
    _write_environment_metadata(
        tmp_path,
        "python/b",
        environment_id="python:duplicate",
        registry_root="registry-b",
    )

    with pytest.raises(EnvironmentResolutionError, match="Duplicate environment id"):
        resolve_environment_registry(
            repo_root=tmp_path,
            runtime_profile=_runtime_profile(registry_roots=("registry-a", "registry-b")),
        )


def test_environment_registry_rejects_roots_that_resolve_to_same_path(tmp_path: Path) -> None:
    (tmp_path / "environments").mkdir()

    with pytest.raises(EnvironmentResolutionError, match="duplicate paths"):
        resolve_environment_registry(
            repo_root=tmp_path,
            runtime_profile=_runtime_profile(registry_roots=("environments", "./environments")),
        )


def test_environment_library_rejects_invalid_metadata_mapping(tmp_path: Path) -> None:
    environment_dir = tmp_path / "environments" / "broken"
    environment_dir.mkdir(parents=True)
    (environment_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (environment_dir / "environment.yaml").write_text("- not-a-mapping\n", encoding="utf-8")

    with pytest.raises(EnvironmentResolutionError, match="metadata must be a mapping"):
        resolve_environment_registry(repo_root=tmp_path, runtime_profile=_runtime_profile())


def test_environment_resolution_rejects_incompatible_tool_version(tmp_path: Path) -> None:
    scenario_path = _repo_root() / "scenarios/python-code-task-baseline/v001/scenario.yaml"
    environment_dir = tmp_path / "environments/python/3.12"
    environment_dir.mkdir(parents=True)
    shutil.copy2(
        _repo_root() / "environments/python/3.12/Dockerfile",
        environment_dir / "Dockerfile",
    )
    (environment_dir / "environment.yaml").write_text(
        """id: python:3.12
version: 1
image: raidar/python:3.12-code
build:
  dockerfile: Dockerfile
capabilities:
  runtimes:
    python: ">=3.12"
  package_managers:
    pip: ">=24"
  tools:
    git: ">=2"
    pytest: ">=8"
    coverage: ">=7"
    ruff: ">=0.14"
    lizard: ">=1.17"
  browsers: {}
""",
        encoding="utf-8",
    )
    scenario = load_scenario(scenario_path)

    with pytest.raises(EnvironmentResolutionError, match="tools.pytest >=9"):
        _resolve_environment(scenario, scenario_path, tmp_path)


def test_resolved_scorer_preserves_inventory_requirements() -> None:
    scenario_path = _repo_root() / "scenarios/python-code-task-baseline/v001/scenario.yaml"
    scenario = load_scenario(scenario_path)
    scorer = next(
        scorer for scorer in scenario.resolved_scorers() if scorer.id == "python-code-task"
    )

    assert scorer.requirements.runtimes == {"python": ">=3.12"}
    assert scorer.requirements.tools == {
        "coverage": ">=7",
        "lizard": ">=1.17",
        "pytest": ">=9",
        "ruff": ">=0.14",
    }


def test_combined_capability_requirements_include_scenario_and_scorer() -> None:
    scenario_path = _repo_root() / "scenarios/python-code-task-baseline/v001/scenario.yaml"
    scenario = load_scenario(scenario_path)
    environment = _resolve_environment(scenario, scenario_path, _repo_root())

    combined = combined_capability_requirements(scenario=scenario, environment=environment)

    assert combined.runtimes["python"] == ">=3.12"
    assert combined.package_managers == {}
    assert combined.tools["pytest"] == ">=9"
    assert combined.tools["coverage"] == ">=7"


def test_capability_version_matching_supports_exact_and_lower_bounds() -> None:
    assert capability_spec_satisfies(">=20", ">=18")
    assert capability_spec_satisfies(">20", ">19")
    assert capability_spec_satisfies("3.12.0", "3.12")
    assert capability_spec_satisfies("playwright", "playwright")
    assert normalize_probe_version("Python 3.12.12") == "3.12.12"

    assert not capability_spec_satisfies(">=18", ">18")
    assert not capability_spec_satisfies("playwright", "selenium")
    assert not capability_spec_satisfies(">=20", "<=20")


def test_scenario_environment_rejects_legacy_shape() -> None:
    scenario_path = _repo_root() / "scenarios/python-code-task-baseline/v001/scenario.yaml"
    payload = load_scenario(scenario_path).model_dump(mode="json")
    payload["environment"].pop("kind")

    with pytest.raises(ValueError, match="Field required"):
        ScenarioDefinition.model_validate(payload)


def test_tool_catalog_exposes_probe_and_presence_contracts() -> None:
    payload = tool_catalog_payload()

    assert payload["runtimes.python"]["probe"] == ["python", "--version"]
    assert probe_command("tools", "git") == ["git", "--version"]
    assert installed_probe_value("browsers", "chromium") == "installed"

    with pytest.raises(ToolCatalogError, match="tools.missing"):
        probe_command("tools", "missing")
    with pytest.raises(ToolCatalogError, match="tools.missing"):
        installed_probe_value("tools", "missing")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("- not-a-mapping\n", "must be a mapping"),
        ("{}\n", "missing tools mapping"),
        ("tools:\n  tools: []\n", "category must be a mapping"),
        ("tools:\n  tools:\n    git: []\n", "entry must be a mapping"),
        ("tools:\n  tools:\n    git:\n      probe: []\n", "must define probe argv"),
        (
            "tools:\n  tools:\n    git:\n      probe: [git, --version]\n      installed_value: 1\n",
            "installed_value must be text",
        ),
    ],
)
def test_tool_catalog_rejects_malformed_catalogs(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    catalog_path = tmp_path / "tools.yaml"
    catalog_path.write_text(content, encoding="utf-8")

    with pytest.raises(ToolCatalogError, match=message):
        _load_tool_catalog(catalog_path)


def test_tool_catalog_rejects_missing_catalog(tmp_path: Path) -> None:
    with pytest.raises(ToolCatalogError, match="Tool catalog not found"):
        _load_tool_catalog(tmp_path / "missing.yaml")


def test_harness_registry_defines_install_and_usage_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "raidar.harness.definitions._local_cli_version",
        lambda command: "1.2.3",
    )

    codex = harness_definition("codex-cli")
    cursor = harness_definition("cursor")

    assert codex.npm_install_spec() == "@openai/codex@1.2.3"
    assert codex.cache_payload()["usage_policy"]["required"] is True
    assert cursor.npm_install_spec() is None
    assert cursor.dockerfile_install_fragment() == ""
    assert cursor.usage_policy.supported is False

    with pytest.raises(HarnessDefinitionError, match="Unknown harness"):
        harness_definition("unknown")


def test_harness_local_version_probe_handles_missing_and_failed_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_run(*_args, **_kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(harness_definitions.subprocess, "run", missing_run)
    assert harness_definitions._local_cli_version(("missing", "--version")) is None

    monkeypatch.setattr(
        harness_definitions.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    assert harness_definitions._local_cli_version(("bad", "--version")) is None
