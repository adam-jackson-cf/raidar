"""Coverage contract for the full two-AgentSpec scenario matrices."""

from pathlib import Path

from raidar.application.scenario_catalog import load_scenario
from raidar.application.scenarios import scenario_revision_paths
from raidar.matrix import load_matrix_config, resolve_matrix_jobs

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_ROOT = REPO_ROOT / "scenarios"
MATRICES_ROOT = REPO_ROOT / "matrices"
MATRIX_SUFFIX = "-codex-gpt55-terra-low.yaml"
EXPECTED_AGENTS = (
    ("codex-cli", "openai", "gpt-5.5", "low"),
    ("codex-cli", "openai", "gpt-5.6-terra", "low"),
    ("codex-cli", "openai", "gpt-5.6-luna", "low"),
)


def test_every_active_benchmark_scenario_revision_has_a_warm_two_agent_matrix() -> None:
    scenario_roots = sorted(path for path in SCENARIOS_ROOT.iterdir() if path.is_dir())

    for scenario_root in scenario_roots:
        revision_paths = scenario_revision_paths(scenario_root)
        revisions = tuple(path.parent.name for path in revision_paths)
        if not revisions:
            continue
        scenario = load_scenario(revision_paths[0])
        if scenario.evaluation_role != "benchmark" or scenario.catalog_status != "active":
            continue
        config_path = MATRICES_ROOT / f"{scenario_root.name}{MATRIX_SUFFIX}"
        assert config_path.is_file(), f"Missing full AgentSpec matrix: {config_path}"

        config = load_matrix_config(config_path)
        jobs = resolve_matrix_jobs(
            config,
            repo_root=REPO_ROOT,
            experiment_kind="benchmark",
        )
        assert (
            tuple(job.scenario.scenario_revision for job in jobs[:: len(EXPECTED_AGENTS)])
            == revisions
        )
        assert len(jobs) == len(revisions) * len(EXPECTED_AGENTS)

        for offset in range(0, len(jobs), len(EXPECTED_AGENTS)):
            revision_jobs = jobs[offset : offset + len(EXPECTED_AGENTS)]
            assert len({job.scenario.scenario_revision for job in revision_jobs}) == 1
            assert (
                tuple(
                    (
                        job.agent.harness,
                        job.agent.provider,
                        job.agent.model,
                        job.agent.reasoning_effort,
                    )
                    for job in revision_jobs
                )
                == EXPECTED_AGENTS
            )
