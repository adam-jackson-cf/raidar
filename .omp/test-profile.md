---
{
  "tiers":
    {
      "t1":
        {
          "selector": "-m 'not slow and not quality and not docker and not security and not perf'",
          "location": "orchestrator/tests",
          "command": "env -u AGENT make test-fast"
        },
      "t2":
        {
          "selector": "-m '(slow or quality or docker) and not security and not perf'",
          "location":
            [
              "orchestrator/tests",
              "review-surface/tests",
              "review-surface/journeys"
            ],
          "command": "env -u AGENT make test-slow test-quality test-docker review-surface-test; each journey: node scripts/surface/lever.mjs --data-dir DATA --evidence-dir EVIDENCE --journey review-surface/journeys/NAME.journey.mjs"
        },
      "t3":
        {
          "selector": "-m 'security or perf'",
          "location": "orchestrator/tests",
          "command": "env -u AGENT make security-gate perf-gate"
        }
    },
  "public_entry_points":
    {
      "raidar": "raidar.cli:main",
      "review-surface": "review-surface/server.mjs"
    },
  "run_events":
    {
      "commit": "env -u AGENT make gate-commit",
      "ci": "env -u AGENT make gate-ci",
      "nightly_scheduler":
        {
          "none": "no scheduled workflow exists in .github/workflows; T2 runs in full on every push and pull request through gate-ci, and Docker/security/perf run at phase exit",
          "approval": "omp-amends.md §1.7 (owner-approved autonomous plan, 2026-11-11); owner to confirm limits"
        },
      "nightly_branches": "main only (no scheduler today; see nightly_scheduler)"
    },
  "fast_tier_budget":
    {
      "duration_report_command": "env -u AGENT make test-fast (the reference pytest plugin writes the T1 JUnit report var/tmp/guardrails/INVOCATION/t1-junit.xml with omp.revision, omp.runtime and omp.wall_s; make guardrails-reference runs omp-guardrails report-check over it)",
      "machines":
        [
          {
            "id": "m-118e13922e9d",
            "platform": "Darwin arm64",
            "runtime": "python 3.13.7",
            "wall_limit_s": 95,
            "per_test_limit_s": 1.02,
            "approval": "omp-amends.md §1.7 (owner-approved autonomous plan, 2026-11-11); owner to confirm limits"
          }
        ]
    },
  "coverage":
    {
      "scope":
        [
          "orchestrator/src",
          "contract/src",
          "lab/stages/raidar/define/tool/src",
          "lab/stages/raidar/verify/tool/src"
        ],
      "line_floor": 99.43,
      "branch_floor": 98.72,
      "where": "orchestrator/pyproject.toml [tool.raidar.coverage]; integer-count enforcement in orchestrator/src/raidar/cli/quality.py"
    },
  "guards":
    {
      "process": "orchestrator/tests/process_guard.py, loaded by orchestrator/tests/conftest.py (audit hook; exempt markers slow, quality, docker, security, perf)",
      "network": "pinned reference pytest adapter plugin (--omp-guard fast-tier; omp_guardrails.adapters.pytest.guards, IP sockets), armed for every T1 run by make test-fast and make quality through PYTEST_ADDOPTS from omp-guardrails adapter junit-args --tier t1; adapter guard-evidence checks the T1 JUnit report",
      "user_data": "same plugin (--omp-guard user-data, TEST.user-data-guard), armed in the same T1 runs through adapter junit-args; points HOME and XDG_* at a temporary directory"
    },
  "environments":
    {
      "required":
        [
          "host Python 3.13.7 with uv 0.10.0 and orchestrator dev extras",
          "Node 22 with root npm ci --ignore-scripts; review-surface npm install and Playwright Chromium",
          "Docker target from ~/.config/raidar/targets.toml (override RAIDAR_TARGETS_FILE) with the built Python pack image, for docker/security/perf"
        ],
      "require_flag": "RAIDAR_REQUIRE_DOCKER=1",
      "readiness_probe": "env -u AGENT make docker-check (raidar target ensure); in tests tests.docker_trial_support.docker_target_ready(), 30 s; review surface: the lever's doctor step"
    },
  "verification_surface":
    {
      "lever_command": "node scripts/surface/lever.mjs",
      "doctor_marker": "RAIDAR-SURFACE-DOCTOR-FAILED",
      "feature_map_path": "docs/feature-map",
      "journey_file_pattern": "review-surface/journeys/*.journey.mjs",
      "data_dir_arg": "--data-dir",
      "evidence_dir_arg": "--evidence-dir"
    },
  "ownership_areas":
    {
      "map":
        {
          "orchestrator/tests/test_affected_seam_gate_quality.py": "repository-gate",
          "orchestrator/tests/test_agent_liveness_monitor_docker.py": "raidar.engine",
          "orchestrator/tests/test_agent_liveness_monitor_slow.py": "raidar.engine",
          "orchestrator/tests/test_analysis_bound_prefix.py": "raidar.scoring",
          "orchestrator/tests/test_analysis_command.py": "raidar.scoring",
          "orchestrator/tests/test_analysis_solvable_excluded_steps.py": "raidar.scoring",
          "orchestrator/tests/test_assessment_store_concurrency.py": "raidar.scoring",
          "orchestrator/tests/test_code_quality_boundaries_slow.py": "raidar.scoring",
          "orchestrator/tests/test_code_quality_coverage_slow.py": "raidar.scoring",
          "orchestrator/tests/test_code_quality_fork_parity_slow.py": "raidar.scoring",
          "orchestrator/tests/test_code_quality_scorer_slow.py": "raidar.scoring",
          "orchestrator/tests/test_define_shim.py": "define_tool",
          "orchestrator/tests/test_dependency_resolver_slow.py": "raidar.grading",
          "orchestrator/tests/test_experiment_schema_rules_quality.py": "raidar.design",
          "orchestrator/tests/test_framework_evidence_safety_slow.py": "raidar.scoring",
          "orchestrator/tests/test_gate_base_context.py": "raidar.subject",
          "orchestrator/tests/test_grader_identity_and_canary_slow.py": "raidar.grading",
          "orchestrator/tests/test_grading_execution_identity_slow.py": "raidar.contract",
          "orchestrator/tests/test_health_evidence.py": "raidar.engine",
          "orchestrator/tests/test_jev_client_and_cache_slow.py": "raidar.judge",
          "orchestrator/tests/test_make_targets.py": "repository-gate",
          "orchestrator/tests/test_mypy_guardrails_quality.py": "repository-gate",
          "orchestrator/tests/test_offline_coverage_runs_slow.py": "raidar.grading",
          "orchestrator/tests/test_overhead_budget_check.py": "raidar.engine",
          "orchestrator/tests/test_package_lock_slow.py": "raidar.subject",
          "orchestrator/tests/test_pre_fix_snapshot_grading_slow.py": "raidar.grading",
          "orchestrator/tests/test_primitive_calls.py": "raidar.subject",
          "orchestrator/tests/test_primitive_gate.py": "raidar.subject",
          "orchestrator/tests/test_primitive_install.py": "raidar.contract",
          "orchestrator/tests/test_primitive_payload_supervisor_slow.py": "raidar.subject",
          "orchestrator/tests/test_problem_bulk.py": "raidar.design",
          "orchestrator/tests/test_problem_dependencies.py": "raidar.design",
          "orchestrator/tests/test_problem_errata_slow.py": "raidar.engine",
          "orchestrator/tests/test_problem_import_behaviors.py": "raidar.design",
          "orchestrator/tests/test_problem_spec_determinacy_slow.py": "raidar.design",
          "orchestrator/tests/test_problem_validation_behaviors_slow.py": "raidar.design",
          "orchestrator/tests/test_process_guard_quality.py": "repository-gate",
          "orchestrator/tests/test_python_fanout_check.py": "repository-gate",
          "orchestrator/tests/test_records_manifest.py": "raidar.engine",
          "orchestrator/tests/test_release_bump_version.py": "release",
          "orchestrator/tests/test_release_register_quality.py": "release",
          "orchestrator/tests/test_release_register_slow.py": "release",
          "orchestrator/tests/test_repository_gate_quality.py": "repository-gate",
          "orchestrator/tests/test_review_projection_from_runs.py": "raidar.evaluation",
          "orchestrator/tests/test_review_projection_integrity.py": "raidar.evaluation",
          "orchestrator/tests/test_ruff_full_quality.py": "repository-gate",
          "orchestrator/tests/test_rule_evidence_freshness.py": "repository-gate",
          "orchestrator/tests/test_rule_evidence_quality.py": "repository-gate",
          "orchestrator/tests/test_run_scoped_docker_environment_slow.py": "raidar.engine",
          "orchestrator/tests/test_smoke_admission_stored_slow.py": "raidar.engine",
          "orchestrator/tests/test_stage_authoring_cli_slow.py": "raidar.cli",
          "orchestrator/tests/test_stage_container_lifecycle.py": "raidar.subject",
          "orchestrator/tests/test_step_reset_docker.py": "raidar.engine",
          "orchestrator/tests/test_step_reset_script.py": "raidar.engine",
          "orchestrator/tests/test_structure_check.py": "repository-gate",
          "orchestrator/tests/test_submission_dependencies_slow.py": "raidar.grading",
          "orchestrator/tests/test_target_loss_job_guard_slow.py": "raidar.engine",
          "orchestrator/tests/test_trial_execution_slow.py": "raidar.engine",
          "orchestrator/tests/test_trial_planning_slow.py": "raidar.design",
          "orchestrator/tests/test_verifier_and_regrade.py": "raidar.grading",
          "orchestrator/tests/test_verifier_bundle_and_isolation_slow.py": "raidar.grading",
          "orchestrator/tests/test_verifier_copy_sandbox_docker.py": "raidar.grading",
          "orchestrator/tests/test_verifier_docker_boundary.py": "raidar.grading",
          "orchestrator/tests/test_verifier_grading_behaviors_slow.py": "raidar.grading",
          "orchestrator/tests/test_verifier_plugin_behaviors_slow.py": "raidar.grading",
          "orchestrator/tests/test_workflow_package_runner.py": "raidar.subject",
          "orchestrator/tests/test_workflow_runner_slow.py": "raidar.subject",
          "orchestrator/tests/test_workspace_seed_slow.py": "raidar.engine",
          "review-surface/journeys/plan-experiment.journey.mjs": "raidar.cli",
          "review-surface/journeys/*.journey.mjs": "review-surface",
          "review-surface/tests/*.spec.ts": "review-surface"
        },
      "affected_t2_selection": "omp-guardrails affected --base origin/main (reads this map and structure.owners; from the step-4 shadow PR); until cutover gate-commit runs scripts/checks/run-affected-seams.py (seam_owners, local slow/quality only)"
    },
  "identity_artefacts":
    {
      "none": "Raidar keeps no execution-identity record and no identity-bearing artefacts. A code change that invalidates experiments means those experiments are rerun, and every experiment run records the raidar git commit it ran on, so results that predate a change are knowable (owner decision 2026-11-12).",
      "approval": "Owner merge of Raidar PR A (remove execution-identity tracking), explicitly authorised by the owner on 2026-11-12."
    },
  "layout":
    {
      "support_dir": "orchestrator/tests (reusable modules named *_support.py; tests/process_guard.py)",
      "fixtures_dir": "orchestrator/tests/fixtures",
      "test_file_patterns":
        [
          "orchestrator/tests/test_*.py",
          "review-surface/tests/*.spec.ts",
          "review-surface/journeys/*.journey.mjs"
        ]
    },
  "command_notes":
    {
      "working_directory": "repository root; prefix make, uv and pytest calls with env -u AGENT; provision with env -u AGENT uv sync --project orchestrator --all-extras --frozen; generated files stay under RAIDAR_VAR (default var/), REPO_TMP_DIR (default var/tmp/) and REPO_CACHE_DIR (default .cache/)",
      "interpretation": "make targets exit non-zero on any failure; test-fast also fails below the coverage floors (integer counts); omp-guardrails report-check exits 1 on a TEST.budget.* finding (a local timing breach on an approved machine, or a missing, invalid or stale report in any mode) and 2 when it cannot run; the lever exits 0 doctor and journeys passed, 1 a journey failed, 2 usage error, 3 doctor failed (prints RAIDAR-SURFACE-DOCTOR-FAILED; an environment failure, never expectRed)",
      "lsp":
        [
          {
            "stack": "typescript",
            "server": "typescript-language-server",
            "install": "npm install -g typescript typescript-language-server",
            "root": "repository root (package.json): init canary FAILED on 2026-11-11, no TypeScript in the root workspace and the global TypeScript 7 install ships no tsserver",
            "canary": "review-surface/ (own package.json and typescript devDependency): references src/api/client.ts:20 api -> 1 location, ok on 2026-11-11"
          },
          {
            "stack": "python",
            "server": "pyright-langserver",
            "install": "npm install -g pyright, or uv tool install pyright",
            "root": "not detected at the repository root (no root pyproject.toml); run lsp from orchestrator/",
            "canary": "orchestrator/: references src/raidar/cli/__init__.py:25 main -> 3 locations, ok on 2026-11-11"
          }
        ]
    },
  "adapter": "pytest",
  "structure":
    {
      "owners":
        {
          "files":
            {
              "scripts/checks/check-repository-paths.py": "repository-gate",
              "scripts/checks/run-affected-seams.py": "repository-gate",
              "scripts/release/bump-version.py": "release"
            },
          "directories":
            {
              "contract/src/raidar_contract/": "raidar_contract",
              "contract/src/raidar_contract/package/": "raidar_contract",
              "lab/stages/raidar/define/tool/src/define_tool/": "define_tool",
              "lab/stages/raidar/verify/tool/src/verify_tool/": "verify_tool",
              "orchestrator/src/raidar/cli/": "raidar.cli",
              "orchestrator/src/raidar/contract/": "raidar.contract",
              "orchestrator/src/raidar/design/": "raidar.design",
              "orchestrator/src/raidar/design/experiment/": "raidar.design",
              "orchestrator/src/raidar/design/problem/": "raidar.design",
              "orchestrator/src/raidar/engine/": "raidar.engine",
              "orchestrator/src/raidar/engine/health/": "raidar.engine",
              "orchestrator/src/raidar/engine/job/": "raidar.engine",
              "orchestrator/src/raidar/engine/perf/": "raidar.engine",
              "orchestrator/src/raidar/engine/target/": "raidar.engine",
              "orchestrator/src/raidar/engine/trial/": "raidar.engine",
              "orchestrator/src/raidar/evaluation/": "raidar.evaluation",
              "orchestrator/src/raidar/evaluation/analysis/": "raidar.evaluation",
              "orchestrator/src/raidar/evaluation/ledger/": "raidar.evaluation",
              "orchestrator/src/raidar/evaluation/review/": "raidar.evaluation",
              "orchestrator/src/raidar/evaluation/review/projection/": "raidar.evaluation",
              "orchestrator/src/raidar/evaluation/review/quality/": "raidar.evaluation",
              "orchestrator/src/raidar/grading/": "raidar.grading",
              "orchestrator/src/raidar/grading/coverage/": "raidar.grading",
              "orchestrator/src/raidar/grading/pre_fix/": "raidar.grading",
              "orchestrator/src/raidar/grading/runtime/": "raidar.grading",
              "orchestrator/src/raidar/judge/": "raidar.judge",
              "orchestrator/src/raidar/scoring/": "raidar.scoring",
              "orchestrator/src/raidar/scoring/catalog/": "raidar.scoring",
              "orchestrator/src/raidar/scoring/code_quality/": "raidar.scoring",
              "orchestrator/src/raidar/scoring/code_quality/snapshot_tool/": "raidar.scoring",
              "orchestrator/src/raidar/scoring/correctness/": "raidar.scoring",
              "orchestrator/src/raidar/scoring/coverage_inputs/": "raidar.scoring",
              "orchestrator/src/raidar/scoring/definition_coverage/": "raidar.scoring",
              "orchestrator/src/raidar/scoring/framework/": "raidar.scoring",
              "orchestrator/src/raidar/scoring/judge/": "raidar.scoring",
              "orchestrator/src/raidar/scoring/process_checks/": "raidar.scoring",
              "orchestrator/src/raidar/scoring/resource_usage/": "raidar.scoring",
              "orchestrator/src/raidar/scoring/test_coverage/": "raidar.scoring",
              "orchestrator/src/raidar/scoring/verification_effort/": "raidar.scoring",
              "orchestrator/src/raidar/subject/": "raidar.subject",
              "orchestrator/src/raidar/subject/agent/": "raidar.subject",
              "orchestrator/src/raidar/subject/agent/adapters/": "raidar.subject",
              "orchestrator/src/raidar/subject/environment/": "raidar.subject",
              "orchestrator/src/raidar/subject/environment/python/": "raidar.subject",
              "orchestrator/src/raidar/subject/package/": "raidar.subject",
              "orchestrator/src/raidar/subject/primitive/": "raidar.subject",
              "orchestrator/src/raidar/subject/primitive/payload/": "raidar.subject",
              "orchestrator/src/raidar/subject/workflow/": "raidar.subject",
              "review-surface/": "review-surface",
              "review-surface/src/": "review-surface",
              "review-surface/src/api/": "review-surface",
              "review-surface/src/components/": "review-surface",
              "review-surface/src/pages/": "review-surface",
              "review-surface/src/pages/wireframe-components/": "review-surface",
              "review-surface/src/presentation/": "review-surface",
              "review-surface/src/quality/": "review-surface",
              "review-surface/src/quality/components/": "review-surface",
              "scripts/checks/": "repository-gate",
              "scripts/release/": "release",
              "scripts/setup/": "repository-gate",
              "scripts/surface/": "review-surface",
              "scripts/vendor/setup-repository-guardrails/": "repository-gate"
            }
        },
      "tiers":
        [
          "raidar.contract",
          "raidar.judge",
          "raidar.subject",
          "raidar.grading",
          "raidar.scoring",
          "raidar.design",
          "raidar.engine",
          "raidar.evaluation",
          "raidar.cli"
        ],
      "import_direction":
        {
          "package": "raidar",
          "source_root": "orchestrator/src",
          "native":
            {
              "command":
                ["lint-imports", "--config", ".importlinter", "--no-cache"],
              "cwd": "orchestrator",
              "timeout_s": 120
            }
        }
    },
  "house_style":
    {
      "version": 1,
      "exceptions":
        [
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/cli/quality.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.1"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/grading/bundle.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.1"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/grading/payload.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.1"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/grading/sandbox.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.1"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/scoring/process_checks/work_session.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.1"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/scoring/process_checks/work_tests.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.1"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/scoring/test_coverage/__init__.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.1"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/subject/primitive/gate.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.1"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/subject/primitive/gate_tool.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.1"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/subject/workflow/judgeloop.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.1"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/subject/workflow/package_checks.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.1"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "lab/stages/raidar/verify/fixtures/*",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.2"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/engine/trial/environment.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.3"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/grading/verifier.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.3"
          },
          {
            "rule": "TEST.no-evasion",
            "path": "orchestrator/tests/process_guard.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.4"
          },
          {
            "rule": "TEST.no-evasion",
            "path": "orchestrator/tests/test_make_targets.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.4"
          },
          {
            "rule": "TEST.no-evasion",
            "path": "orchestrator/tests/test_overhead_fixture_run.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.4"
          },
          {
            "rule": "TEST.no-evasion",
            "path": "orchestrator/tests/test_primitive_calls.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.4"
          },
          {
            "rule": "TEST.no-evasion",
            "path": "orchestrator/tests/test_primitive_payload_supervisor_slow.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.4"
          },
          {
            "rule": "TEST.no-evasion",
            "path": "orchestrator/tests/test_step_reset_script.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.4"
          },
          {
            "rule": "TEST.no-evasion",
            "path": "orchestrator/tests/test_verifier_bundle_and_isolation_slow.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.4"
          },
          {
            "rule": "TEST.no-evasion",
            "path": "orchestrator/tests/test_workflow_verify_docker.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.4"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/conftest.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/experiment_spec_support.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/package_support.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/primitive_support.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/stage_check_support.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_agent_boundary_outcomes.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_agent_process_reaping.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_analysis_cli_coverage.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_analysis_outcomes.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_analysis_primary_outcome.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_analysis_reporting_boundaries.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_analysis_verification_effort.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_assessment_store_behaviors.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_assessment_store_concurrency.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_claude_code_cli_adapter.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_cli_commands.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_cli_execution_outcomes.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_code_quality_scorer.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_coverage_scorer_readings.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_declared_wheels_docker.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_dependency_resolver.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_detached_supervisor_outcomes.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_experiment_lock_behaviors.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_gate_tool_witness.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_generated_artifact_guard.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_grading_execution_identity.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_harness_contracts.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_health_coverage_close.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_health_judge.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_imports.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_jev_coverage_closure.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_jev_judge_parity.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_job_admission_outcomes.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_job_completion_outcomes.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_mypy_guardrails_quality.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_native_agent_api.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_native_agents.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_offline_coverage_decisions.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_offline_coverage_runs_slow.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_offline_grading_concurrency.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_overhead_report_gate.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_package_identity.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_package_lock.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_package_never_runs_on_host.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_package_stage_install.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_plan_outcomes.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_pooling_gate.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_pre_fix_grading_decisions.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_primitive_calls_shape.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_primitive_install.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_primitive_outputs.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_primitive_payload_boundaries.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_primitive_payload_supervisor_slow.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_primitive_stop.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_problem_bulk.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_problem_cli_decisions.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_problem_errata_slow.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_problem_record_decisions.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_problem_validation_behaviors.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_problem_validation_behaviors_slow.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_problem_validation_decisions.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_process_compliance.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_python_pack_image.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_python_pack_provisioning.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_quality_gate_commands.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_registry_inspection_commands.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_release_cutover.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_release_register.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_repository_contract_boundaries.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_rescore_store_bindings.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_run_scoped_docker_environment.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_runtime_gate_refusals.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_stage_authoring_cli.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_stage_command_outcomes.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_stage_package.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_submission_dependencies.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_submission_dependencies_slow.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_target_loss_job_guard.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_target_selection_and_trust.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_target_tunnel_and_ensure.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_tool_tree_hashing.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_trial_configuration_outcomes.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_trial_execution.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_trial_execution_slow.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_trial_health_audit.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_trial_lifecycle_outcomes.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_trial_materialisation_outcomes.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_trial_provision_outcomes.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_verifier_and_regrade.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_verifier_docker_boundary.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_verifier_grading_behaviors.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_verifier_regrade_decisions.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_workflow_judging.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_workflow_package.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_workflow_package_runner.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/test_workflow_runner.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-self-mocking",
            "path": "orchestrator/tests/verifier_support.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "lab/stages/raidar/define/tool/src/define_tool/compact.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "lab/stages/raidar/define/tool/src/define_tool/enumerated.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "lab/stages/raidar/define/tool/src/define_tool/featuredef.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "lab/stages/raidar/define/tool/src/define_tool/normative.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "lab/stages/raidar/define/tool/src/define_tool/tool.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/cli/stage.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.3"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/design/problem/resolver.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.3"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/engine/health/admission.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/evaluation/analysis/manipulation.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/scoring/catalog/builtin.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.3"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/scoring/code_quality/arithmetic.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/scoring/code_quality/lineage.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/scoring/judge/gating.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/subject/agent/harness.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          },
          {
            "rule": "TEST.no-test-hooks",
            "path": "orchestrator/src/raidar/subject/workflow/identity.py",
            "adr": "docs/adr/adr-003-guardrails-reference-exceptions.md \u00a72.5"
          }
        ]
    },
  "guardrails":
    {
      "structure":
        {
          "root_directories":
            [
              ".agents",
              ".claude",
              ".github",
              ".omp",
              "contract",
              "docs",
              "lab",
              "orchestrator",
              "review-surface",
              "scripts",
              "var"
            ],
          "root_files":
            [
              ".gitignore",
              ".mailmap",
              ".pre-commit-config.yaml",
              ".python-version",
              "AGENTS.md",
              "CHANGELOG.md",
              "CLAUDE.md",
              "CODEOWNERS",
              "Makefile",
              "README.md",
              "package-lock.json",
              "package.json",
              "skills-lock.json",
              "split-design.md"
            ],
          "areas":
            {
              "lab":
                {
                  "directories":
                    [
                      "agents",
                      "environments",
                      "experiments",
                      "judge",
                      "ledger",
                      "problems",
                      "stage-checks",
                      "stages",
                      "workflows"
                    ],
                  "files": ["imports.yaml", "releases.yaml"]
                }
            },
          "packages":
            [
              {
                "root": "orchestrator/src/raidar",
                "children":
                  {
                    "cli": [],
                    "contract": [],
                    "judge": [],
                    "design": ["experiment", "problem"],
                    "subject":
                      [
                        "agent",
                        "environment",
                        "package",
                        "primitive",
                        "workflow"
                      ],
                    "engine": ["health", "job", "perf", "target", "trial"],
                    "grading": ["coverage", "pre_fix", "runtime"],
                    "scoring":
                      [
                        "catalog",
                        "code_quality",
                        "correctness",
                        "coverage_inputs",
                        "definition_coverage",
                        "framework",
                        "judge",
                        "process_checks",
                        "resource_usage",
                        "test_coverage",
                        "verification_effort"
                      ],
                    "evaluation": ["analysis", "ledger", "review"]
                  },
                "max_depth": 3
              }
            ],
          "instances":
            [
              {
                "root": "lab/workflows",
                "depth": 2,
                "marker": "workflow.yaml"
              },
              {
                "root": "lab/stages",
                "depth": 2,
                "marker": "stage.yaml",
                "exempt": ["lab/stages/README.md"]
              }
            ],
          "forbidden_imports":
            [{ "root": "contract/src", "modules": ["harbor", "raidar"] }]
        },
      "naming":
        {
          "ecosystems":
            {
              "python":
                [
                  "orchestrator/src",
                  "contract/src",
                  "lab/stages/raidar/define/tool/src",
                  "lab/stages/raidar/verify/tool/src"
                ],
              "asset_id": [".agents/skills"]
            },
          "skip":
            [
              "scripts/vendor",
              "orchestrator/tests/fixtures/guardrails",
              "lab/stages/raidar/verify/fixtures"
            ],
          "version_exempt": ["docs"],
          "symbol_exceptions":
            {
              "scripts/checks/check-test-evidence.py":
                [
                  "visit_Name",
                  "visit_FunctionDef",
                  "visit_AsyncFunctionDef",
                  "visit_JoinedStr",
                  "visit_Call"
                ],
              "scripts/checks/check-analyzer-scope.py":
                [
                  "visit_Import",
                  "visit_ImportFrom",
                  "visit_Assign",
                  "visit_AnnAssign",
                  "visit_FunctionDef",
                  "visit_AsyncFunctionDef",
                  "visit_ClassDef",
                  "visit_Call",
                  "visit_Attribute",
                  "visit_Name"
                ],
              "scripts/checks/check-test-hygiene.py":
                [
                  "visit_Import",
                  "visit_ImportFrom",
                  "visit_FunctionDef",
                  "visit_AsyncFunctionDef",
                  "visit_AnnAssign",
                  "visit_Assign",
                  "visit_With",
                  "visit_Call"
                ],
              "orchestrator/src/raidar/scoring/judge/codebase.py":
                [
                  "visit_Module",
                  "visit_ClassDef",
                  "visit_Call",
                  "visit_Attribute",
                  "visit_Name"
                ],
              "orchestrator/tests/test_jev_client_and_cache_slow.py":
                ["do_POST"]
            }
        },
      "python":
        {
          "maintained_roots":
            [
              "contract/src/raidar_contract",
              "contract/conformance",
              "orchestrator/src/raidar",
              "orchestrator/tests",
              "scripts/checks",
              "scripts/release",
              ".agents/skills",
              "lab/stages/*/*/tool/src"
            ],
          "excluded_paths":
            [
              "scripts/vendor/setup-repository-guardrails/validate_rule_coverage.py",
              "orchestrator/tests/fixtures/guardrails",
              "lab/stages/raidar/verify/fixtures",
              "docs",
              "lab/experiments",
              "var",
              ".cache",
              "orchestrator/.cache",
              "orchestrator/.venv"
            ],
          "script_roots": ["scripts/checks", "scripts/release"],
          "script_names":
            [
              "bump-version.py",
              "check-analyzer-scope.py",
              "check-bespoke-rule-canary.py",
              "check-finding-baseline.py",
              "check-gate-rule-canary.py",
              "check-hook-installation.py",
              "check-mypy-coverage.py",
              "check-python-fanout.py",
              "check-python-quality.py",
              "check-repository-paths.py",
              "check-ruff-suppressions.py",
              "check-rule-evidence.py",
              "check-structure.py",
              "check-test-evidence.py",
              "check-test-hygiene.py",
              "check-tool-rule-canary.py",
              "check-toolchain-parity.py",
              "check_toolchain_pins.py",
              "record-host-readback.py",
              "run-affected-seams.py",
              "run-repository-gate.py",
              "validate-rule-coverage.py",
              "verify-version-wiring.py"
            ],
          "script_name_exceptions":
            [
              "scripts/checks/check_toolchain_pins.py",
              "scripts/checks/ruff_baseline.py"
            ],
          "test_roots": ["orchestrator/tests"],
          "test_groups":
            {
              "behaviour": ["orchestrator/tests/test_*.py"],
              "support":
                [
                  "orchestrator/tests/*_support.py",
                  "orchestrator/tests/process_guard.py"
                ],
              "fixtures":
                [
                  "orchestrator/tests/fixtures/**",
                  "orchestrator/tests/review_fixtures/**"
                ]
            },
          "test_root_files":
            [
              "orchestrator/tests/__init__.py",
              "orchestrator/tests/conftest.py"
            ],
          "generic_name_allowlist": [],
          "non_package_dirs":
            [
              "checks",
              "orchestrator",
              "raidar",
              "release",
              "scripts",
              "src",
              "tests"
            ],
          "data_label_dirs":
            [
              "lab/stages/*",
              "lab/stages/*/*",
              "orchestrator/tests/fixtures/stages/*",
              "orchestrator/tests/fixtures/stages/*/*"
            ],
          "skill_roots": [".agents/skills"],
          "class_limits":
            {
              "max_lines": 700,
              "max_init_args": 8,
              "max_instance_attributes": 12,
              "max_public_methods": 20
            },
          "process_roots":
            [
              "orchestrator/src/raidar",
              "contract/src/raidar_contract",
              "lab/stages",
              "scripts/checks",
              "scripts/release"
            ],
          "subprocess_wrappers":
            {
              "lab/stages/raidar/verify/tool/src/verify_tool/checks.py":
                ["run"],
              "orchestrator/src/raidar/cli/agent.py": ["agent_setup_auth"],
              "orchestrator/src/raidar/cli/env_harbor.py":
                ["compose_preflight_reason", "env_setup"],
              "orchestrator/src/raidar/cli/process.py": ["run_or_raise"],
              "orchestrator/src/raidar/cli/repo_state.py":
                [
                  "has_unstaged_changes",
                  "repo_name_status_from_git_cmd",
                  "repo_paths_from_git_cmd"
                ],
              "orchestrator/src/raidar/cli/stage_lock.py": ["_uv"],
              "orchestrator/src/raidar/design/problem/variant_shim.py":
                ["main"],
              "orchestrator/src/raidar/engine/job/detach.py":
                ["launch", "supervise"],
              "orchestrator/src/raidar/engine/target/transport.py":
                ["run_command", "spawn_detached"],
              "orchestrator/src/raidar/engine/trial/cleanup.py": ["_run"],
              "orchestrator/src/raidar/engine/trial/environment.py":
                [
                  "RunScopedDockerEnvironment._resolve_main_container",
                  "RunScopedDockerEnvironment._run_engine_command",
                  "RunScopedDockerEnvironment.download_dir_with_exclusions"
                ],
              "orchestrator/src/raidar/grading/closure.py": ["run_docker"],
              "orchestrator/src/raidar/grading/resolver.py":
                ["UvResolver._compile", "UvResolver.identity"],
              "orchestrator/src/raidar/grading/sandbox.py":
                [
                  "DockerCopySandbox._exec",
                  "DockerCopySandbox._start_and_wait",
                  "sweep_grader_orphans"
                ],
              "orchestrator/src/raidar/scoring/code_quality/measurement.py":
                ["run_process"],
              "orchestrator/src/raidar/subject/environment/python/image.py":
                ["run_docker"],
              "orchestrator/src/raidar/subject/environment/python/provisioner.py":
                ["run_command"],
              "orchestrator/src/raidar/subject/primitive/gate_tool.py":
                ["_git", "_spawn", "_version", "prerequisites"],
              "orchestrator/src/raidar/subject/primitive/payload/install_tree.py":
                ["_run"],
              "orchestrator/src/raidar/subject/release_check.py": ["_git"],
              "orchestrator/src/raidar/subject/workflow/package_checks.py":
                ["uv_lock_stale"],
              "orchestrator/src/raidar/subject/workflow/stage_container.py":
                ["PackContainer._run"],
              "scripts/checks/check-bespoke-rule-canary.py":
                ["run_fanout_checker"],
              "scripts/checks/check-finding-baseline.py": ["main"],
              "scripts/checks/check-gate-rule-canary.py": ["run_process"],
              "scripts/checks/check-hook-installation.py": ["run_process"],
              "scripts/checks/check-mypy-coverage.py": ["main"],
              "scripts/checks/check-python-fanout.py": ["_run_git"],
              "scripts/checks/check-python-quality.py": ["_check_baseline"],
              "scripts/checks/check-repository-paths.py":
                ["dependency_findings", "main"],
              "scripts/checks/check-ruff-suppressions.py": ["main"],
              "scripts/checks/check-structure.py": ["repo_paths"],
              "scripts/checks/check-test-evidence.py": ["_git_bytes"],
              "scripts/checks/check-tool-rule-canary.py": ["analyzer_canary"],
              "scripts/checks/check-toolchain-parity.py": ["run_process"],
              "scripts/checks/check_toolchain_pins.py":
                ["_registry_resolver_violations"],
              "scripts/checks/record-host-readback.py": ["main"],
              "scripts/checks/ruff_baseline.py": ["main"],
              "scripts/checks/run-affected-seams.py": ["main"],
              "scripts/checks/validate-rule-coverage.py": ["main"],
              "scripts/release/bump-version.py":
                [
                  "get_commits_since_last_bump",
                  "git_output",
                  "render_lock_update"
                ],
              "scripts/release/verify-version-wiring.py": ["_git"]
            },
          "dynamic_import_roots":
            [
              "orchestrator/src/raidar",
              "contract/src/raidar_contract",
              "lab/stages"
            ],
          "dynamic_import_adapters":
            [
              "orchestrator/src/raidar/grading/runtime/raidar_oracle_plugin.py",
              "orchestrator/src/raidar/scoring/code_quality/measurement.py",
              "orchestrator/src/raidar/scoring/framework/registry.py"
            ],
          "contract_files":
            [
              "contract/src/raidar_contract/agent.py",
              "contract/src/raidar_contract/call.py",
              "orchestrator/src/raidar/judge/protocol.py",
              "orchestrator/src/raidar/subject/agent/protocol.py",
              "orchestrator/src/raidar/scoring/framework/context.py"
            ],
          "contract_data_annotations":
            {
              "orchestrator/src/raidar/judge/protocol.py":
                { "parse_questions.raw": "Any", "parse_answers.raw": "Any" },
              "orchestrator/src/raidar/scoring/framework/context.py":
                {
                  "StepInputs.agent_result": "Mapping[str, Any] | None",
                  "AssessContext.config": "Mapping[str, Any]",
                  "AssessContext.problem_config": "Mapping[str, Any]"
                },
              "orchestrator/src/raidar/subject/agent/protocol.py":
                { "NativeAgent.harness_isolation": "dict[str, Any] | None" }
            },
          "loop_roots":
            [
              "orchestrator/src/raidar",
              "contract/src/raidar_contract",
              "lab/stages",
              "scripts/checks",
              "scripts/release"
            ],
          "max_loop_depth": 2,
          "analyzer_like_roots":
            [
              "orchestrator/src/raidar/scoring",
              "orchestrator/src/raidar/evaluation/analysis",
              "orchestrator/src/raidar/engine/health"
            ],
          "ownership_markers": ["CODEOWNERS"],
          "source_roots":
            ["orchestrator/src", "contract/src", "lab/stages/*/*/tool/src"],
          "fanout":
            {
              "root_packages":
                ["define_tool", "raidar", "raidar_contract", "verify_tool"],
              "architectural_max": 10,
              "large_module_nloc": 800,
              "large_module_architectural_max": 3
            }
        },
      "test_evidence":
        { "fast_tier": "t1", "approved_skips": [], "unselected_exempt": [] },
      "coverage_ratchet":
        {
          "floor_keys":
            [
              "fail_under",
              "fail-under",
              "cov-fail-under",
              "lines",
              "branches",
              "statements",
              "functions",
              "line_floor",
              "branch_floor"
            ],
          "exclusion_keys":
            ["omit", "exclude_lines", "exclude_also", "partial_branches"],
          "approvals": []
        },
      "golden_record": {},
      "test_growth": { "map_path": ".omp/test-behavior-map.json" },
      "feature_map": { "index_files": [] },
      "journeys": {},
      "guarded_run": { "watch": [".config/raidar"] },
      "lever_selftest": { "timeout_s": 120 },
      "adapter_pytest": { "collect_timeout_s": 300 }
    }
}
---

# Repo test profile

Global `test-guidance` sets the test rules; this profile holds Raidar's values. The front matter
above holds the machine-readable values that `omp-guardrails profile-check` (`PROFILE.complete`)
and the reference checkers read; keep it and the sections below in step. `none` is allowed only
in Verification surface, Nightly scheduler and Identity-bearing artefacts, with an owner approval.
Identity-bearing artefacts is `none`: Raidar keeps no execution-identity record or review ceremony.

## Tiers

- T1 fast: no marker; selector
  `not slow and not quality and not docker and not security and not perf`;
  location `orchestrator/tests/`; command `env -u AGENT make test-fast` (coverage on, floors
  enforced, `--durations=10`). Drives logic, schemas, decisions, the CLI through `CliRunner`,
  and process, git or Docker shells through an injected runner. A few small files in `tmp_path`
  are fine.
- T2 slow (`slow`): selector `slow and not quality and not docker`; command
  `env -u AGENT make test-slow`. Real `git`, `bash`, `sh`, `python`, `uv` or `make` processes,
  `supervise` forks, real run directories, loopback servers and real-clock waits. Never Docker.
- T2 quality (`quality`): selector `quality and not docker`; command
  `env -u AGENT make test-quality`. Canaries proving a quality rule accepts and refuses, in
  isolated tool processes and workspaces.
- T2 Docker (`docker`): selector `docker and not security and not perf`; command
  `env -u AGENT make test-docker` (a ready target and the pack image; no real models).
- T2 review surface: Playwright `chromium` project in `review-surface/tests/*.spec.ts`;
  command `env -u AGENT make review-surface-test`.
- T2 journeys: `review-surface/journeys/*.journey.mjs`, each run through the lever (see
  Verification surface).
- T3 security and perf (`security`, `perf`, both on top of `docker`): commands
  `env -u AGENT make security-gate` and `env -u AGENT make perf-gate`, at phase exit.
  Perf targets `orchestrator/tests/test_overhead_fixture_run.py` at concurrency `1` and `3`.
- Tier selection for `TEST.tier-selectors`: each front-matter `tiers.<tier>.selector` holds the
  pytest arguments that `omp-guardrails adapter tiers` collects with. The pytest adapter cannot
  list the Playwright specs or the journeys, so `make guardrails-reference` appends the
  Playwright `--list` output for `review-surface/tests/*.spec.ts` and every
  `review-surface/journeys/*.journey.mjs` file to the T2 list before
  `omp-guardrails collection-check`.
- Markers apply per file (`pytestmark`); never mix tiers in one file. Marker registry:
  `orchestrator/pyproject.toml` (`--strict-markers`); file inventory
  `scripts/checks/repository-owners.json` field `test_tiers`.
- Public entry point per package: `raidar`: `raidar.cli:main` (the `raidar` CLI);
  `review-surface`: `review-surface/server.mjs` (HTTP API under `/api/` plus the built SPA).

## Run events

- Commit command: `env -u AGENT make gate-commit` (`.omp/gate.json` `checks.commit`): path
  policy, test evidence, rule coverage, `quality` (includes T1), the pinned reference
  (`make guardrails-reference`, which runs `omp-guardrails report-check` for `TEST.budget.*` in
  local mode), dependency audit and affected local slow/quality seams.
- CI command: `env -u AGENT make gate-ci` (`.omp/gate.json` `checks.ci`; required context
  `gate`, runner role `github-actions`): the commit gate with `AUDIT_ARGS=--ci`, then full
  slow and quality.
- Nightly scheduler: none. No scheduled workflow exists. Approval: omp-amends.md §1.7
  (owner-approved autonomous plan, 2026-11-11); the owner is to confirm it (front matter records
  the reason). Docker, security, perf and review-surface tiers run outside the gate.
- Nightly branches: `main` only, once a scheduler exists.
- Separate workflow: `.github/workflows/perf-fixture.yml` (`make perf-fixture`, target `ci`).

## Fast-tier budget

- Per machine: `m-118e13922e9d` (probe of `omp_guardrails.test_evidence.budgets`), platform
  `Darwin arm64`, runtime `python 3.13.7`.
- Per machine limits: wall `95 s`, per-test `1.02 s`. Derived as `ceil(1.25 x max wall)` and
  `ceil_10ms(1.5 x slowest test)` over 5 local `env -u AGENT make test-fast` runs on 2026-11-11
  through the reference pytest plugin (guards armed, coverage on, 1 worker, 5694 tests): walls
  (`omp.wall_s`) 74.566, 75.284, 74.002, 73.686, 74.648 s; slowest test 0.662 to 0.679 s.
- Per machine owner approval reference: omp-amends.md §1.7 (owner-approved autonomous plan,
  2026-11-11); the owner is to confirm the limits. The limits block locally on this machine.
- CI timing is report-only on every CI host. Goals, not limits: wall `30 s`, per-test `0.1 s`.
- Duration-report command: `env -u AGENT make test-fast` (or `make quality`); the reference
  pytest plugin (`omp-guardrails adapter junit-args --tier t1`) writes the T1 JUnit report
  `var/tmp/guardrails/$GUARDRAILS_INVOCATION/t1-junit.xml` with `omp.revision`, `omp.runtime`
  and `omp.wall_s`. Checker: `omp-guardrails report-check --mode local` in `make gate-commit`
  and `--mode ci` in `make gate-ci`, both through `make guardrails-reference`, both blocking.
- New machine or runtime: report-check prints the owner question with this run's wall and
  slowest test; measure repeatedly and record the approved limits in `fast_tier_budget.machines`.

## Coverage

- Scope: T1 over `orchestrator/src`, `contract/src` and every `lab/stages/*/*/tool/src` beside a
  `stage.yaml`; fixture programs are inputs, outside the denominator.
- Line floor: `99.43`; branch floor: `98.72`.
- Where defined: `orchestrator/pyproject.toml` `[tool.raidar.coverage]`; integer-count
  enforcement in `orchestrator/src/raidar/cli/quality.py`. Artefacts under
  `var/tmp/tests/coverage/`.
- Floors are met only by outcome or property cases or deleting unreachable code; never a pragma,
  a wider `omit` or a lower floor. Stage tool sources stay in coverage.

## Guards

- Fast-tier process guard location: `orchestrator/tests/process_guard.py`, loaded by
  `orchestrator/tests/conftest.py`; denies `subprocess.Popen`, `os.system`, `os.posix_spawn`,
  `os.fork`, `os.forkpty`, `os.exec*`, `os.spawn*`, `pty.spawn` outside `slow`, `quality`,
  `docker`, `security`, `perf`, fixtures and teardown included.
- Fast-tier network guard location: the pinned reference's pytest adapter plugin
  (`--omp-guard fast-tier`), armed for every T1 run by `make test-fast` and `make quality`
  through `PYTEST_ADDOPTS` from `omp-guardrails adapter junit-args --tier t1`. It denies IP
  network traffic and process creation; `adapter guard-evidence` checks the T1 JUnit report.
- User-data guard: the same plugin (`--omp-guard user-data`), armed in the same T1 runs; it
  points `HOME` and `XDG_*` at a temporary directory.

## Environments

- Required environments: host Python `3.13.7`, uv `0.10.0`, orchestrator dev extras; Node `22`
  with root `npm ci --ignore-scripts`; review surface npm packages and Playwright Chromium;
  a Docker target and the built Python pack image for Docker, security and perf.
- Docker targets: `~/.config/raidar/targets.toml`, override `RAIDAR_TARGETS_FILE`; select one
  with `TARGET=` on the make command.
- Require flag: `RAIDAR_REQUIRE_DOCKER=1` (a missing target fails instead of skipping).
- Readiness probe: `env -u AGENT make docker-check` (`raidar target ensure`); in tests
  `tests.docker_trial_support.docker_target_ready()`, `30 s`, once per session. Review surface:
  the lever's doctor step.

## Verification surface

- Lever command: `node scripts/surface/lever.mjs` (launch, doctor, drive, evidence, cleanup for
  the review surface UI and the orchestrator CLI).
- Doctor failure marker: `RAIDAR-SURFACE-DOCTOR-FAILED` (exit `3`); never `expectRed`.
- Feature map path: `docs/feature-map/`.
- Journey file pattern: `review-surface/journeys/*.journey.mjs`.
- Data directory argument: `--data-dir` (empty or absent; `RAIDAR_VAR` and `TMPDIR` point there).
- Evidence directory argument: `--evidence-dir`.

## Ownership areas

- Map of T2 files to areas: front matter `ownership_areas.map`. Areas are the
  `raidar.<group>` packages, `raidar_contract`, `define_tool`, `verify_tool`, `review-surface`,
  `repository-gate` and `release`. Seeded from `scripts/checks/repository-owners.json`:
  `seam_owners` first, then each test file's most-imported group, then reviewed overrides for
  canaries with no package import.
- Production owners: front matter `structure.owners.directories`, from `source_directories`
  plus the review surface and scripts.
- Affected T2 test selection: `omp-guardrails affected --base origin/main` from the step-4
  shadow PR. Until cutover `gate-commit` runs `scripts/checks/run-affected-seams.py`.
- Unknown ownership never narrows a run: an unowned changed production file selects all T2.

## Identity-bearing artefacts

- None (front matter `identity_artefacts`: `none` with the owner's approval). Raidar keeps no
  execution-identity record and no identity review or revalidation ceremony.
- Experiment locks and problem validations still pin the code digests they ran under (grader
  implementation, `primitive_code`). A checkout whose code differs is refused (RT7, plan
  checks); that refusal is how an invalidating change shows up.
- A code change that invalidates an experiment makes that experiment invalid: re-validate the
  refused problems, relock through `make experiment-lock` and rerun it. Each experiment run
  records the raidar git commit it ran on, so results that predate a change are knowable.
- Approval: the owner's merge of Raidar PR A (remove execution-identity tracking), explicitly
  authorised by the owner on 2026-11-12.
- Nothing under `orchestrator/src` or `lab/stages` may be shaped to one problem or experiment.

## Layout and naming

- Support directory: `orchestrator/tests/`, reusable modules `*_support.py`.
- Fixtures directory: `orchestrator/tests/fixtures/`; stage inputs `lab/stages/*/*/fixtures/`;
  review fixture builder `orchestrator/tests/review_fixtures/`.
- Test file patterns: `orchestrator/tests/test_*.py`, `review-surface/tests/*.spec.ts`,
  `review-surface/journeys/*.journey.mjs`.
- Placement check until cutover: `scripts/checks/check-repository-paths.py`.

## Structure and house style

- Capability owners: front matter `structure.owners`.
- Tiers, lowest first, and import direction: `raidar.contract`, `raidar.judge`,
  `raidar.subject`, `raidar.grading`, `raidar.scoring`, `raidar.design`, `raidar.engine`,
  `raidar.evaluation`, `raidar.cli`; source root `orchestrator/src`; native engine
  `lint-imports --config .importlinter` in `orchestrator/`.
- House-style version and ADR-backed exceptions: version `1`; front matter
  `house_style.exceptions` lists exact `(rule, path)` waivers for the R1 findings of the new
  rule IDs, each backed by `docs/adr/adr-003-guardrails-reference-exceptions.md`.

## Command notes

- Working directory and prerequisites: repository root; prefix `make`, `uv` and `pytest` with
  `env -u AGENT`; provision with `env -u AGENT uv sync --project orchestrator --all-extras
--frozen` (CI adds `--python 3.13.7`); install the hook with `env -u AGENT uv run --project
orchestrator --extra dev pre-commit install --hook-type pre-commit`.
- Flags, exit codes, and interpretation: front matter `command_notes.interpretation`.
- LSP (init, 2026-11-11): `typescript-language-server` fails the canary at the repository root
  because the root workspace has no TypeScript and the global TypeScript 7 install has no
  tsserver. It passes from `review-surface/` (`src/api/client.ts:20 api`, 1 reference).
  `pyright-langserver` is not detected at the root (no root `pyproject.toml`); from
  `orchestrator/` it passes (`src/raidar/cli/__init__.py:25 main`, 3 references).
- Find pilot (`omp find`, cwd repository root, 2026-11-11, judge role `openrouter/typesafe/jev-1.14` from the omp config):

  | Query (scope, keywords)                                                                                                                                          | Expected files                                                                                  | Recall                       | Cost                            |
  | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | ---------------------------- | ------------------------------- |
  | Where reviewer annotations on a run are created, saved and loaded (`review-surface`; `annotation`, `note`, `label`, `comment`)                                   | `server.mjs`, `src/api/client.ts` (both badly named), `src/components/AnnotationCreateForm.tsx` | 3/3                          | $0.0015, 10 requests, 92 judged |
  | Keep the judge API key out of agent and grading sandbox containers (`orchestrator/src`; `OPENROUTER_API_KEY`, `secret`, `key`, `leak`, `sandbox`, `environment`) | `judge/secrets.py`, `grading/sandbox.py`, `engine/trial/environment.py` (badly named)           | 2/3; missed `environment.py` | $0.0014, 9 requests, 128 judged |
  | Kill an idle agent that produces no output and uses no CPU (`orchestrator/src`; `idle`, `liveness`, `heartbeat`, `kill`, `stall`, `hung`, `timeout`)             | `engine/health/liveness.py`, `engine/trial/driver.py` (installs the monitor)                    | 1/2; missed `driver.py`      | $0.0015, 8 requests, 128 judged |

  Total recall 6/8 at $0.0044. Every miss is a caller of a found symbol (`require_key_absent`,
  `install_liveness_monitor`), which the usage rule's follow-up `lsp references` step covers.
