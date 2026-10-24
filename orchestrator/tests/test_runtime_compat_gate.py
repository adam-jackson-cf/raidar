"""The runtime-compatibility gate: RT0-RT6 at lock (and plan/run), AS1 at assess.

Every code is exercised directly and through the bypass vectors the gate must
close: ``--set`` overrides, local ``path:`` problems, one-shot refs, a lock's
runtime at plan time, and ``assess --rescore``/``--regrade`` arguments.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.components.definition.registry import ComponentResolver
from raidar.packs import UnknownPackError, load_runtime_pack
from raidar.packs.drift import LockedPackError, lock_runtime_pack, pack_drift_issues
from raidar.packs.python.image import image_tag
from raidar.packs.python.models import PackSpec
from raidar.problems.importer import _runtime_pack
from raidar.problems.resolver import ProblemResolver
from raidar.problems.store import ProblemStoreError
from raidar.scorers.catalog import scorer_resolver
from raidar.spec.loader import CliOverrides, load_experiment
from raidar.spec.lock import LOCK_VERSION, LockError, build_lock, seal_lock
from raidar.spec.metrics import MetricResolver
from raidar.spec.references import RefKind, RegistryRef, ResolverSet
from tests.experiment_spec_support import (
    InMemoryResolver,
    experiment_document,
    write_experiment,
)
from tests.verifier_support import make_bundle

FORGE = "scb/forge@abc123"
FORGE_NODE = "scb-node-ts/forge@abc123"
ONESHOT_NODE = "scb-node-ts/circuit_eval.checkpoint_1@abc123"
PYTHON_BINDING = {"runtime": "python", "pack_version": 1, "validated": True}
NODE_BINDING = {"runtime": "node-ts", "pack_version": 1, "validated": True}
PackLoader = Callable[[str], PackSpec]


def _resolvers(
    *,
    problems: dict[str, dict[str, object]] | None = None,
    components: dict[str, dict[str, object]] | None = None,
    scorers: dict[str, dict[str, object]] | None = None,
) -> ResolverSet:
    problem_bindings = {FORGE: PYTHON_BINDING, FORGE_NODE: NODE_BINDING, ONESHOT_NODE: NODE_BINDING}
    return ResolverSet(
        [
            InMemoryResolver(
                RefKind.PROBLEM,
                {
                    FORGE: {"checkpoints": 8},
                    FORGE_NODE: {"checkpoints": 8},
                    ONESHOT_NODE: {"checkpoints": 1},
                },
                bindings={**problem_bindings, **(problems or {})},
            ),
            InMemoryResolver(
                RefKind.COMPONENT,
                {"rules-file@1": {"kind": "workspace"}, "lint-config@1": {"kind": "workspace"}},
                bindings={
                    "lint-config@1": {"runtimes": ["python"], "supports": ["codex-cli"]},
                    **(components or {}),
                },
            ),
            InMemoryResolver(
                RefKind.SCORER,
                {"correctness@1": {"exact": True}, "code-quality@3": {"exact": True}},
                bindings={"code-quality@3": {"runtimes": ["python"]}, **(scorers or {})},
            ),
            MetricResolver(),
        ],
        entry_point_group=None,
    )


def _node_pack(runtime: str) -> PackSpec:
    """A stand-in node-ts pack (same contract as python) so RT3/RT4 can be isolated."""
    if runtime not in {"python", "node-ts"}:
        raise UnknownPackError(f"no runtime pack for {runtime!r} (packs: node-ts, python)")
    return load_runtime_pack("python").model_copy(update={"id": runtime})


def _lock(
    path: Path,
    *assignments: str,
    resolvers: ResolverSet | None = None,
    pack_loader: PackLoader | None = None,
) -> dict[str, object]:
    loaded = load_experiment(path, CliOverrides(assignments=assignments))
    return build_lock(
        loaded,
        lock_path=path.with_name("experiment.lock.json"),
        resolvers=resolvers or _resolvers(),
        pack_loader=pack_loader,
    )


def _issues(path: Path, *assignments: str, **kwargs: Any) -> dict[str, str]:
    with pytest.raises(LockError) as caught:
        _lock(path, *assignments, **kwargs)
    issues: dict[str, str] = {}
    for issue in caught.value.issues:
        issues[issue.loc] = f"{issues.get(issue.loc, '')} {issue.message}".strip()
    return issues


@pytest.fixture
def experiment(tmp_path: Path) -> Path:
    return write_experiment(tmp_path / "exp", experiment_document())


def test_a_compatible_design_locks_without_recording_gate_facts(experiment: Path) -> None:
    lock = _lock(experiment)

    problem = lock["execution"]["references"]["problem"][FORGE]  # type: ignore[index]
    assert "runtime" not in problem["metadata"]
    assert "validated" not in problem["metadata"]
    assert lock["execution"]["design"]["runtime"] == "python"  # type: ignore[index]


# ---- RT1 and its bypass vectors -------------------------------------------------


@pytest.mark.parametrize("ref", [FORGE_NODE, ONESHOT_NODE])
def test_rt1_a_set_override_cannot_swap_in_another_runtimes_binding(
    experiment: Path, ref: str
) -> None:
    issues = _issues(experiment, f"problems.0.ref={ref}")

    assert issues["problems.0.ref"].startswith(
        f"[RT1] {ref} is bound to pack node-ts; experiment runtime is python"
    )


def _task(root: Path, metadata: str) -> None:
    task = root / "tasks" / "forge"
    task.mkdir(parents=True)
    (task / "task.toml").write_text(f'{metadata}[[steps]]\nname = "checkpoint_1"\n')


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ("", "declares no runtime; add [metadata] raidar_runtime"),
        ('[metadata]\nraidar_runtime = "node-ts"\n', "is for runtime node-ts"),
    ],
)
def test_rt1_a_local_path_task_must_declare_the_experiments_runtime(
    tmp_path: Path, metadata: str, message: str
) -> None:
    _task(tmp_path, metadata)
    path = write_experiment(tmp_path, experiment_document(problems=[{"path": "tasks/forge"}]))

    issues = _issues(path)

    assert "[RT1]" in issues["problems.0.path"]
    assert message in issues["problems.0.path"]


def test_rt1_a_local_task_of_the_right_runtime_locks(tmp_path: Path) -> None:
    _task(tmp_path, '[metadata]\nraidar_runtime = "python"\n')
    path = write_experiment(tmp_path, experiment_document(problems=[{"path": "tasks/forge"}]))

    assert _lock(path)["execution"]["problems"][0]["label"] == "path:tasks/forge"  # type: ignore[index]


def test_rt1_a_binding_without_a_runtime_is_refused(experiment: Path) -> None:
    resolvers = _resolvers(problems={FORGE: {"pack_version": 1, "validated": True}})

    issues = _issues(experiment, resolvers=resolvers)

    assert "[RT1] scb/forge@abc123 reports no binding runtime" in issues["problems.0.ref"]


# ---- RT0, RT2 ---------------------------------------------------------------------


def test_rt0_an_unvalidated_binding_is_refused(experiment: Path) -> None:
    binding = {**PYTHON_BINDING, "validated": False, "validation_issue": "verdict excluded"}

    issues = _issues(experiment, resolvers=_resolvers(problems={FORGE: binding}))

    assert (
        "[RT0] scb/forge@abc123 has no passing verifier validation: verdict excluded"
        in (issues["problems.0.ref"])
    )


def test_rt2_a_binding_of_another_pack_contract_is_stale(experiment: Path) -> None:
    binding = {**PYTHON_BINDING, "pack_version": 0}

    issues = _issues(experiment, resolvers=_resolvers(problems={FORGE: binding}))

    assert (
        "[RT2] stale binding: scb/forge@abc123 was imported against pack python@0"
        in (issues["problems.0.ref"])
    )


# ---- RT3, RT4 -------------------------------------------------------------------


def test_rt3_components_must_cover_the_runtime_and_support_the_arms_agent(
    tmp_path: Path,
) -> None:
    document = experiment_document(id="node-ts__fixture__test", runtime="node-ts")
    document["problems"] = [{"ref": FORGE_NODE}]
    document["conditions"][1]["components"].append({"use": "lint-config@1", "files": []})
    document["shared_components"] = [{"use": "rules-file@1", "files": ["components/AGENTS.md"]}]
    document["conditions"][1]["components"] = document["conditions"][1]["components"][1:]
    path = write_experiment(tmp_path, document)
    claude_only = {"runtimes": ["*"], "supports": ["claude-code"]}

    issues = _issues(
        path,
        resolvers=_resolvers(components={"rules-file@1": claude_only}),
        pack_loader=_node_pack,
    )

    assert (
        "[RT3] lint-config@1 supports runtimes [python]; experiment runtime is node-ts"
        in (issues["conditions.1.components.0.use"])
    )
    assert (
        "[RT3] rules-file@1 does not support agent codex (arm baseline runs it)"
        in (issues["shared_components.0.use"])
    )


def test_rt4_scorers_and_the_metric_must_cover_the_runtime(tmp_path: Path) -> None:
    document = experiment_document(id="node-ts__fixture__test", runtime="node-ts")
    document["problems"] = [{"ref": FORGE_NODE}]
    document["assessment"] = {"scorers": ["correctness@1", "code-quality@3"]}
    path = write_experiment(tmp_path, document)

    issues = _issues(path, pack_loader=_node_pack)

    assert "[RT4] code-quality@3 supports runtimes [python]" in issues["assessment.scorers.1"]
    assert (
        "[RT4] headline metric scb-canonical@1 measures quality components for runtimes "
        in (issues["assessment.metric"])
    )
    assert "assessment.scorers.0" not in issues


# ---- RT5 ------------------------------------------------------------------------


def test_rt5_a_set_override_cannot_move_an_agent_off_the_pack_pin(experiment: Path) -> None:
    issues = _issues(experiment, "agents.0.version='0.1.0'")

    assert issues["agents.0.version"] == (
        "[RT5] codex 0.1.0 is not the pin in pack python@1 (0.156.1)"
    )


def test_rt5_an_agent_the_pack_does_not_bake_in_is_refused(experiment: Path) -> None:
    issues = _issues(
        experiment,
        "agents.0.name=gemini-cli",
        "agents.0.provider=google",
        "agents.0.model=gemini-3-flash-preview",
        "agents.0.reasoning_effort=",
    )

    assert issues["agents.0.name"] == "[RT5] pack python@1 bakes no agent gemini-cli"


# ---- RT6 at lock and at plan/run ---------------------------------------------------


def test_rt6_and_every_other_issue_of_a_runtime_override_are_reported_at_once(
    experiment: Path,
) -> None:
    issues = _issues(experiment, "runtime=go", "id=go__fixture__test")

    assert issues["runtime"].startswith("[RT6] no runtime pack for 'go'")
    assert (
        "[RT1] scb/forge@abc123 is bound to pack python; experiment runtime is go"
        in (issues["problems.0.ref"])
    )
    assert "[RT4] headline metric" in issues["assessment.metric"]


def test_rt6_plan_loads_the_pack_of_the_lock_runtime_and_refuses_drift(experiment: Path) -> None:
    lock = json.loads(json.dumps(_lock(experiment)))
    current = image_tag(load_runtime_pack("python"))
    references = lock["execution"]["references"]["problem"]

    references[FORGE]["metadata"]["pack_image"] = current
    assert lock_runtime_pack(lock).id == "python"
    references[FORGE]["metadata"]["pack_image"] = "raidar/pack-python:0123456789abcdef"
    with pytest.raises(LockedPackError, match=r"\[RT6\] pack drift: scb/forge@abc123"):
        lock_runtime_pack(lock)
    lock["spec"]["runtime"] = "rust"
    with pytest.raises(LockedPackError, match=r"\[RT6\] no runtime pack for 'rust'"):
        lock_runtime_pack(lock)


def test_drift_ignores_images_that_are_not_builds_of_the_pack() -> None:
    built = "raidar/pack-python:2b91a52663e099f2"
    images = {"fixture/adder@x": "ghcr.io/astral-sh/uv:python3.12@sha256:abc", "scb/f@y": built}

    assert pack_drift_issues(images, built) == []
    assert pack_drift_issues({"scb/f@y": "raidar/pack-python:ffff"}, built)


def test_the_importer_loads_the_pack_of_the_sources_runtime() -> None:
    assert _runtime_pack("python").id == "python"
    with pytest.raises(ProblemStoreError, match=r"\[RT6\] no runtime pack for 'node-ts'"):
        _runtime_pack("node-ts")
    with pytest.raises(ProblemStoreError, match=r"\[RT1\] source 'scb' binds runtime python"):
        _runtime_pack("python", _node_pack("node-ts"), "scb")


# ---- the real resolvers report the gate facts --------------------------------------


def test_the_real_resolvers_report_binding_facts() -> None:
    repo = Path(__file__).resolve().parents[2]
    problem = ProblemResolver().resolve(
        RegistryRef.parse(RefKind.PROBLEM, "scb/forge@38d627ecf668"), base_dir=repo
    )
    lint = ComponentResolver().resolve(
        RegistryRef.parse(RefKind.COMPONENT, "lint-config@1"), base_dir=repo
    )
    rules = ComponentResolver().resolve(
        RegistryRef.parse(RefKind.COMPONENT, "rules-file@1"), base_dir=repo
    )
    quality = scorer_resolver().resolve(
        RegistryRef.parse(RefKind.SCORER, "code-quality@3"), base_dir=repo
    )

    assert problem.binding == PYTHON_BINDING
    assert lint.binding["runtimes"] == ["python"]
    assert rules.binding["runtimes"] == ["*"]
    assert rules.binding["supports"] == ["codex-cli", "claude-code"]
    assert rules.metadata["supports"] == ["codex", "claude-code"]
    assert quality.binding == {"runtimes": ["python"]}


# ---- AS1: assess-time ---------------------------------------------------------------


def _job(root: Path, runtime: str, bundle_digest: str) -> Path:
    job = root / "jobs" / "e" / "j1"
    (job / "trials" / "t1").mkdir(parents=True)
    lock = seal_lock(
        {
            "lock_version": LOCK_VERSION,
            "experiment": {"id": "e"},
            "spec": {"runtime": runtime, "assessment": {"scorers": ["correctness@1"]}},
            "execution": {
                "references": {
                    "problem": {
                        FORGE_NODE: {"digest": "d", "metadata": {"bundle_sha256": bundle_digest}}
                    }
                }
            },
            "assessment": {},
            "analysis": {},
            "toolchain": {},
        }
    )
    (job / "job.lock.json").write_text(json.dumps(lock))
    manifest = {"kind": "raidar-job", "job_id": "j1", "lock_sha256": lock["lock_sha256"]}
    (job / "job.json").write_text(json.dumps(manifest))
    return job


def _assess(*args: str) -> Any:
    return CliRunner().invoke(main, ["assess", *args])


def test_as1_rescore_scorers_must_cover_the_jobs_runtime(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "b")
    job = _job(tmp_path, "node-ts", bundle.digest())
    store = tmp_path / "store"

    refused = _assess(
        "--rescore", str(job / "trials"), "--store", str(store), "--scorer", "code-quality@3"
    )
    allowed = _assess(
        "--rescore", str(job / "trials"), "--store", str(store), "--scorer", "correctness@1"
    )

    assert refused.exit_code != 0
    assert (
        "[AS1] scorer code-quality@3 supports runtimes [python]; the job's runtime is node-ts"
        in (refused.output)
    )
    assert "[AS1]" not in allowed.output


def test_as1_a_trial_outside_any_job_accepts_only_runtime_agnostic_scorers(
    tmp_path: Path,
) -> None:
    trial = tmp_path / "loose" / "t1"
    trial.mkdir(parents=True)

    refused = _assess(
        "--rescore", str(trial), "--store", str(tmp_path / "s"), "--scorer", "code-quality@3"
    )

    assert refused.exit_code != 0
    assert "the target is outside any job, so its runtime is unknown (pass --lock)" in (
        refused.output
    )


@pytest.mark.parametrize("mode", ["--rescore", "--regrade"])
def test_as1_a_bundle_override_must_be_the_verifier_bundle_the_lock_binds(
    tmp_path: Path, mode: str
) -> None:
    bound = make_bundle(tmp_path / "bound")
    other = make_bundle(tmp_path / "other")
    (other.root / "tests" / "extra.py").write_text("x = 1\n")
    job = _job(tmp_path, "python", bound.digest())
    target = job / "trials" if mode == "--rescore" else job
    store = (
        ["--store", str(tmp_path / "s"), "--scorer", "correctness@1"] if mode == "--rescore" else []
    )

    refused = _assess(mode, str(target), *store, "--bundle", str(other.root))

    assert refused.exit_code != 0
    assert "[AS1] --bundle" in refused.output
    assert "is not the verifier bundle of any problem the job's lock binds" in refused.output
