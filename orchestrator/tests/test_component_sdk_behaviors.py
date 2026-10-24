"""Component SDK contract: registry, manifests, arm rejection, lock resolver."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.contract.references import (
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    ResolverSet,
)
from raidar.design.experiment.arm import ComponentRejectedError, compile_arm, compile_condition
from raidar.design.experiment.loader import load_experiment
from raidar.design.experiment.lock import SPEC_EXTERNAL_MODULES, build_lock
from raidar.scoring.framework.metrics import DEFAULT_METRIC_REGISTRY, MetricResolver
from raidar.subject.agent.config import Harness
from raidar.subject.treatment import (
    ComponentKind,
    ComponentRegistry,
    ComponentResolver,
    UnknownComponentError,
    component_resolver,
    fingerprint,
)
from raidar.subject.treatment.catalog.workspace import RULES_FILE, RulesFile
from raidar.subject.treatment.definition.manifest import ComponentManifest, DiscoveryProbe
from tests.component_support import (
    PILOT_DIR,
    PROBE_DIR,
    agent,
    all_builtin_uses,
    compile_uses,
    condition,
    use,
)
from tests.experiment_spec_support import experiment_document, fixture_resolvers, write_experiment

SDK_RUNTIME_SOURCES = [
    "orchestrator/src/raidar/design/experiment/arm.py",
    "orchestrator/src/raidar/subject/treatment/definition/compiled.py",
    "orchestrator/src/raidar/subject/treatment/runtime/setup_script.py",
    "orchestrator/src/raidar/subject/treatment/runtime/step_reset.sh",
]
BUILTIN_REFS = [
    "continue-on-stall@2",
    "guardrail-gate@4",
    "harness-settings@1",
    "lint-config@1",
    "lint-tool@2",
    "mcp-server@1",
    "prompt-wrapper@1",
    "python-guardrails@8",
    "review-loop@2",
    "rules-file@1",
    "skills@1",
    "stop-hook@1",
    "transient-retry@1",
    "workspace-files@1",
]


def test_builtins_register_under_entry_points_with_complete_manifests() -> None:
    registry = ComponentRegistry()

    assert registry.refs() == BUILTIN_REFS
    kinds = {registry.get(ref).manifest.kind for ref in BUILTIN_REFS}
    assert kinds == {
        ComponentKind.WORKSPACE,
        ComponentKind.HARNESS,
        ComponentKind.PROMPT,
        ComponentKind.WORKFLOW,
        ComponentKind.SERVICE,
    }
    for ref in BUILTIN_REFS:
        manifest = registry.get(ref).manifest
        assert manifest.ref == ref
        assert set(manifest.supports) == {Harness.CODEX_CLI, Harness.CLAUDE_CODE}
        assert manifest.discovery_probe.evidence
        assert fingerprint(registry.get(ref)) == fingerprint(registry.get(ref))
        # Every component digest covers the code that builds and resets its trials.
        assert set(SDK_RUNTIME_SOURCES) <= set(manifest.fingerprint), ref


def test_entry_points_load_lazily_only_the_requested_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded: list[str] = []
    registry = ComponentRegistry()
    original = type(registry._entry_points["rules-file@1"]).load

    def spy(self: object) -> object:
        loaded.append(self.name)  # type: ignore[attr-defined]
        return original(self)  # type: ignore[arg-type]

    monkeypatch.setattr(type(registry._entry_points["rules-file@1"]), "load", spy)
    registry.get("rules-file@1")
    registry.get("rules-file@1")

    assert loaded == ["rules-file@1"]


def test_unknown_component_rejects_the_arm_and_lists_every_issue() -> None:
    with pytest.raises(ComponentRejectedError) as raised:
        compile_uses(
            use("no-such-thing@1"),
            use("rules-file@9"),
            use("rules-file@1", (f"{PROBE_DIR}/AGENTS.md",)),
        )

    issues = raised.value.issues
    assert len(issues) == 2
    assert "components.0 (no-such-thing@1): unknown component" in issues[0]
    assert "components.1 (rules-file@9)" in issues[1]


def test_component_without_mapping_for_the_harness_rejects_the_arm() -> None:
    narrow = RulesFile()
    narrow.manifest = RULES_FILE.manifest.model_copy(update={"supports": (Harness.CODEX_CLI,)})
    registry = ComponentRegistry([narrow], entry_point_group=None)
    uses = condition(use("rules-file@1", ("components/guided/AGENTS.md",)))

    compiled = compile_condition(
        uses, agent(Harness.CODEX_CLI), base_dir=PILOT_DIR, registry=registry
    )
    with pytest.raises(ComponentRejectedError, match="does not support harness claude-code"):
        compile_condition(uses, agent(Harness.CLAUDE_CODE), base_dir=PILOT_DIR, registry=registry)
    with pytest.raises(ComponentRejectedError, match="no component mapping for gemini"):
        compile_condition(condition(), agent(Harness.GEMINI), base_dir=PILOT_DIR)
    assert [item.ref for item in compiled.uses] == ["rules-file@1"]


def test_invalid_use_and_conflicting_uses_reject_the_arm() -> None:
    cases = [
        (use("rules-file@1"), "expects 1 file"),
        (use("rules-file@1", (f"{PROBE_DIR}/AGENTS.md",), extra=1), "unknown 'with' keys"),
        (use("harness-settings@1", settings={"model": "x"}), "not harness-settings"),
        (use("harness-settings@1", settings={"web_search": "live"}), "no discovery probe"),
        (use("lint-config@1", (f"{PROBE_DIR}/AGENTS.md",)), "not a known lint config"),
        (use("review-loop@2", max_review_rounds=9), "integer 1..5"),
        (use("rules-file@1", (PROBE_DIR,)), "must be a file"),
        (use("harness-settings@1"), "missing required 'with' keys"),
        (use("harness-settings@1", settings={}), "non-empty object"),
        (use("stop-hook@1"), "exactly one of"),
        (use("stop-hook@1", command=" "), "non-empty string"),
        (use("mcp-server@1", name="bad name", command="x"), "must match"),
        (use("mcp-server@1", name="ok", command=""), "non-empty string"),
        (use("mcp-server@1", name="ok", command="x", args="y"), "list of strings"),
        (use("skills@1", (PROBE_DIR,)), "directory with SKILL.md"),
        (use("workspace-files@1", (PROBE_DIR,), dest=3), "relative path string"),
        (use("workspace-files@1", (PROBE_DIR,), dest="../x"), "inside the workspace"),
    ]
    for bad, message in cases:
        with pytest.raises(ComponentRejectedError, match=message):
            compile_uses(bad)
    with pytest.raises(ComponentRejectedError, match="more than one workflow"):
        compile_uses(use("review-loop@2"), use("review-loop@2"))
    for duplicate, message in (
        ("prompt-wrapper@1", "more than one prompt template"),
        ("skills@1", "used twice"),
        ("mcp-server@1", "used twice"),
    ):
        pair = [u for u in all_builtin_uses(Harness.CODEX_CLI) if u.use == duplicate] * 2
        with pytest.raises(ComponentRejectedError, match=message):
            compile_uses(*pair)
    with pytest.raises(ComponentRejectedError, match="set twice"):
        compile_uses(
            use("rules-file@1", (f"{PROBE_DIR}/AGENTS.md",)),
            use("workspace-files@1", (f"{PROBE_DIR}/AGENTS.md",)),
        )
    two_hooks = (use("stop-hook@1", command="echo a"), use("stop-hook@1", command="echo b"))
    with pytest.raises(ComponentRejectedError, match="'notify' is set to both"):
        compile_uses(*two_hooks)
    claude = compile_uses(*two_hooks, harness=Harness.CLAUDE_CODE)
    hooks = claude.native_config["hooks"]
    assert isinstance(hooks, dict)
    stop = hooks["Stop"]
    assert isinstance(stop, list)
    assert len(stop) == 2


def test_validate_and_lock_reject_an_arm_the_compiler_would_reject(tmp_path: Path) -> None:
    document = experiment_document()
    document["agents"].append(
        {
            "id": "gem",
            "name": "gemini-cli",
            "provider": "google",
            "model": "gemini-3-flash-preview",
            "version": "0.156.1",
        }
    )
    document["conditions"][1]["components"].append(
        {"use": "review-loop@2", "with": {"max_review_rounds": 9}}
    )
    document["arms"].append({"id": "gem-guided", "condition": "guided", "agent": "gem"})
    document["factors"] = [
        "condition",
        "agent.name",
        "agent.model",
        "agent.provider",
        "agent.reasoning_effort",
    ]
    path = write_experiment(tmp_path, document)
    runner = CliRunner()

    validated = runner.invoke(main, ["validate", str(path)])
    locked = runner.invoke(main, ["lock", str(path)])

    assert validated.exit_code == 1, validated.output
    assert "arms.1: condition 'guided' on agent 'luna': components.1 (review-loop@2)" in (
        validated.output
    )
    assert "arms.2: condition 'guided' on agent 'gem': no component mapping for gemini" in (
        validated.output
    )
    assert "arms.0" not in validated.output
    assert locked.exit_code == 1
    assert "integer 1..5" in locked.output
    assert not (tmp_path / "experiment.lock.json").exists()


def test_validate_accepts_arms_that_compile() -> None:
    result = CliRunner().invoke(main, ["validate", str(PILOT_DIR / "experiment.yaml")])

    assert result.exit_code == 0, result.output


def test_registry_refuses_mismatched_or_duplicate_definitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ComponentRegistry()
    entry = registry._entry_points["rules-file@1"]
    monkeypatch.setitem(registry._entry_points, "skills@1", entry)
    with pytest.raises(RegistryReferenceError, match="loads a definition for 'rules-file@1'"):
        registry.get("skills@1")
    monkeypatch.setattr(type(entry), "load", lambda self: object())
    with pytest.raises(RegistryReferenceError, match="not a component definition"):
        ComponentRegistry().get("lint-config@1")
    monkeypatch.undo()
    doubled = [*registry._entry_points.values(), entry]
    monkeypatch.setattr(
        "raidar.subject.treatment.definition.registry.entry_points", lambda group: doubled
    )
    with pytest.raises(RegistryReferenceError, match="more than one entry point"):
        ComponentRegistry()
    with pytest.raises(RegistryReferenceError, match="duplicate"):
        ComponentRegistry([RULES_FILE, RULES_FILE], entry_point_group=None)
    with pytest.raises(UnknownComponentError, match="registered: none"):
        ComponentRegistry(entry_point_group=None).get("rules-file@1")


def test_resolver_digest_tracks_manifest_and_implementation_sources(tmp_path: Path) -> None:
    resolved = component_resolver().resolve(
        RegistryRef.parse(RefKind.COMPONENT, "rules-file@1"), base_dir=tmp_path
    )
    changed = RulesFile()
    changed.manifest = RULES_FILE.manifest.model_copy(update={"description": "changed"})
    missing = RulesFile()
    missing.manifest = ComponentManifest(
        **{
            **RULES_FILE.manifest.model_dump(),
            "fingerprint": ("orchestrator/src/raidar/subject/treatment/absent.py",),
            "discovery_probe": DiscoveryProbe(id="x", description="x", evidence=("x",)),
        }
    )

    assert resolved.metadata["kind"] == "workspace"
    assert resolved.metadata["discovery_probe"] == "rules-file-loaded"
    assert resolved.digest == fingerprint(RULES_FILE)
    assert fingerprint(changed) != resolved.digest
    with pytest.raises(RegistryReferenceError, match="does not exist"):
        fingerprint(missing)
    with pytest.raises(UnknownComponentError):
        ComponentResolver().resolve(RegistryRef.parse(RefKind.COMPONENT, "x@1"), base_dir=tmp_path)


def test_lock_records_component_digest_through_the_entry_point_resolver(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())
    others = [r for r in fixture_resolvers() if r.kind is not RefKind.COMPONENT]
    resolvers = ResolverSet([MetricResolver(DEFAULT_METRIC_REGISTRY), *others])

    lock = build_lock(load_experiment(path), lock_path=tmp_path / "lock.json", resolvers=resolvers)

    assert isinstance(ResolverSet().resolver_for(RefKind.COMPONENT), ComponentResolver)
    recorded = lock["execution"]["references"]["component"]["rules-file@1"]  # type: ignore[index]
    assert recorded["digest"] == fingerprint(RULES_FILE)
    assert recorded["metadata"]["supports"] == ["codex", "claude-code"]


def test_compile_arm_uses_the_arms_condition_and_agent() -> None:
    loaded = load_experiment(PILOT_DIR / "experiment.yaml")
    guided = compile_arm(loaded.spec, "guided", base_dir=loaded.base_dir)
    baseline = compile_arm(loaded.spec, "baseline", base_dir=loaded.base_dir)

    assert list(guided.overlay) == ["AGENTS.md", "ruff.toml"]
    assert baseline.overlay == {}
    assert guided.digest != baseline.digest
    with pytest.raises(KeyError):
        compile_arm(loaded.spec, "missing", base_dir=loaded.base_dir)


def test_copied_component_file_change_changes_the_compiled_digest(tmp_path: Path) -> None:
    shutil.copytree(PILOT_DIR / "components", tmp_path / "components")
    before = compile_uses(use("rules-file@1", ("components/guided/AGENTS.md",)), base_dir=tmp_path)
    (tmp_path / "components" / "guided" / "AGENTS.md").write_text("# different rules\n")
    after = compile_uses(use("rules-file@1", ("components/guided/AGENTS.md",)), base_dir=tmp_path)

    assert before.digest != after.digest


def test_pilot_arms_share_the_framing_and_guided_adds_the_review_loop_workflow() -> None:
    """Pilot v2: both arms get the same starter framing; only guided adds components."""
    loaded = load_experiment(PILOT_DIR / "experiment.yaml")
    agent = loaded.spec.agent("codex-luna")
    baseline = compile_condition(loaded.spec.condition("baseline"), agent, base_dir=PILOT_DIR)

    compiled = compile_condition(loaded.spec.condition("guided"), agent, base_dir=PILOT_DIR)

    assert [item.ref for item in baseline.uses] == ["prompt-wrapper@1"]
    assert [item.ref for item in compiled.uses] == [
        "prompt-wrapper@1",
        "rules-file@1",
        "skills@1",
        "lint-config@1",
        "stop-hook@1",
        "review-loop@2",
    ]
    assert baseline.prompt_template == compiled.prompt_template
    assert baseline.uses[0].fingerprint == compiled.uses[0].fingerprint
    assert compiled.agent_wrapper is not None
    assert compiled.agent_wrapper.import_path.endswith("review_loop:ReviewLoopAgent")
    assert compiled.agent_wrapper.kwargs["max_review_rounds"] == 2


def test_component_trees_ship_exactly_the_files_the_lock_hashes(tmp_path: Path) -> None:
    from raidar.design.experiment.lock import _hash_tree
    from raidar.subject.treatment.catalog._common import tree_files

    skill = tmp_path / "skills" / "discipline"
    (skill / ".git").mkdir(parents=True)
    (skill / "SKILL.md").write_text("x\n")
    (skill / ".DS_Store").write_bytes(b"\0")
    (skill / ".git" / "HEAD").write_text("ref\n")

    shipped = [path.relative_to(tmp_path).as_posix() for path in tree_files(skill)]
    hashed = sorted(_hash_tree(tmp_path, "skills/discipline"))
    assert shipped == hashed == ["skills/discipline/SKILL.md"]


# Imported modules a fingerprint need not list: the lock's toolchain section
# pins raidar.design.experiment and SPEC_EXTERNAL_MODULES (raidar_spec digest); the registry
# and the harness enum are lookup machinery, not component behaviour.
_UNFINGERPRINTED_OK = {
    "orchestrator/src/raidar/subject/agent/config.py",
    "orchestrator/src/raidar/subject/treatment/definition/registry.py",
}


def test_every_raidar_module_a_builtin_imports_is_fingerprinted() -> None:
    import ast

    from raidar.subject.treatment.definition.registry import ComponentRegistry

    repo = Path(__file__).resolve().parents[2]
    registry = ComponentRegistry()
    missing: dict[str, list[str]] = {}
    for ref in registry.refs():
        listed = set(registry.get(ref).manifest.fingerprint)
        for relative in sorted(item for item in listed if item.endswith(".py")):
            for node in ast.walk(ast.parse((repo / relative).read_text())):
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                if not node.module.startswith("raidar."):
                    continue
                module = "orchestrator/src/" + node.module.replace(".", "/")
                path = next(
                    (c for c in (f"{module}.py", f"{module}/__init__.py") if (repo / c).is_file()),
                    None,
                )
                if (
                    path
                    and path not in listed
                    and path not in _UNFINGERPRINTED_OK
                    and not path.startswith("orchestrator/src/raidar/design/experiment/")
                    and path.removeprefix("orchestrator/src/raidar/") not in SPEC_EXTERNAL_MODULES
                ):
                    missing.setdefault(ref, []).append(path)
    assert missing == {}
