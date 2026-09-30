SHELL := /bin/bash

REPO_TMP_DIR ?= $(CURDIR)/.tmp
REPO_CACHE_DIR ?= $(CURDIR)/.cache
REPO_UV_CACHE_DIR ?= $(REPO_CACHE_DIR)/uv
REPO_RUNTIME_ENV := mkdir -p "$(REPO_TMP_DIR)" "$(REPO_UV_CACHE_DIR)" && env \
	TMPDIR="$(REPO_TMP_DIR)" \
	TMP="$(REPO_TMP_DIR)" \
	TEMP="$(REPO_TMP_DIR)" \
	XDG_CACHE_HOME="$(REPO_CACHE_DIR)" \
	UV_CACHE_DIR="$(REPO_UV_CACHE_DIR)"

RAIDAR := $(REPO_RUNTIME_ENV) uv run --project orchestrator raidar
RAIDAR_DEV := $(REPO_RUNTIME_ENV) uv run --project orchestrator --extra dev raidar

# Shared public workflow defaults.
HARNESS ?=
PROVIDER ?=
MODEL ?=
REASONING_EFFORT ?=
DEVICE_AUTH ?=
EXPERIMENT ?=
EXPERIMENT_SET ?=
LOCK_CHECK ?=
TIMEOUT_SEC ?=

# Experiment-path smoke workflow: examples/smoke/*.yaml are locked under .tmp/smoke with
# the target's overrides and run through Harbor (Docker; OrbStack by default).
SMOKE_EXPERIMENT ?= examples/smoke/experiment.yaml
SMOKE_AGENTS_EXPERIMENT ?= examples/smoke/agents.experiment.yaml
SMOKE_RUNS_DIR ?= $(CURDIR)/runs
SMOKE_LOCK_DIR ?= $(REPO_TMP_DIR)/smoke
SMOKE_BLOCKS ?= 1
SMOKE_CONCURRENCY ?= 1
# smoke-matrix arms: one per harness the Python pack bakes in (examples/smoke/agents.experiment.yaml).
SMOKE_MATRIX_SELECTOR ?=
SMOKE_MATRIX_SELECTORS := codex claude
ifneq ($(strip $(SMOKE_MATRIX_SELECTOR)),)
ifeq ($(SMOKE_MATRIX_SELECTOR),gemini)
$(error SMOKE_MATRIX_SELECTOR 'gemini' is unavailable: the Python pack bakes in only codex-cli and claude-code)
endif
ifeq ($(filter $(SMOKE_MATRIX_SELECTOR),$(SMOKE_MATRIX_SELECTORS)),)
$(error Unknown SMOKE_MATRIX_SELECTOR '$(SMOKE_MATRIX_SELECTOR)'; expected one of: $(SMOKE_MATRIX_SELECTORS))
endif
endif
# agent-smoke: the smoke experiment with its one agent replaced. Harness versions must equal
# the Python pack pins (orchestrator/src/raidar/packs/python/pack.yaml); planning refuses others.
AGENT_SMOKE_HARNESS ?= codex-cli
AGENT_SMOKE_PROVIDER ?= openai
AGENT_SMOKE_MODEL ?= gpt-6-luna
AGENT_SMOKE_REASONING_EFFORT ?= low
AGENT_SMOKE_VERSION_codex-cli := 0.156.1
AGENT_SMOKE_VERSION_claude-code := 2.1.285
AGENT_SMOKE_EFFECTIVE_HARNESS = $(or $(HARNESS),$(AGENT_SMOKE_HARNESS))
AGENT_SMOKE_EFFECTIVE_PROVIDER = $(or $(PROVIDER),$(AGENT_SMOKE_PROVIDER))
AGENT_SMOKE_EFFECTIVE_MODEL = $(or $(MODEL),$(AGENT_SMOKE_MODEL))
AGENT_SMOKE_EFFECTIVE_REASONING = $(or $(REASONING_EFFORT),$(AGENT_SMOKE_REASONING_EFFORT))
AGENT_SMOKE_EFFECTIVE_VERSION = $(or $(HARNESS_VERSION),$(AGENT_SMOKE_VERSION_$(AGENT_SMOKE_EFFECTIVE_HARNESS)))

.PHONY: help \
	env-setup harness-list harness-validate codex-auth-setup harbor-cleanup docker-check \
	experiment-validate experiment-lock experiment-plan experiment-execute perf-check perf-fixture \
	problem-import problem-validate \
	experiment-assess assess-regrade experiment-analyze jev-shadow-rubric \
	jev-export-labels jev-import-labels \
	pack-info pack-build pack-audit pack-tool-provision \
	component-probe-trials \
	smoke-dry-run-check orchestrator-smoke smoke-matrix agent-smoke \
	benchmark-fixture-synthetic review-fixture-synthetic \
	review-surface-data review-surface-build review-surface-serve review-surface-test \
	review-surface-quality-data \
	quality

define require_var
	@if [ -z "$($1)" ]; then \
		echo "Missing required variable: $1"; \
		exit 1; \
	fi
endef

help:
	@echo "Public workflow surface (experiment -> lock -> run -> assess -> analyze -> review)"
	@echo ""
	@echo "Environment:"
	@echo "  make env-setup                                         Bootstrap the orchestrator environment"
	@echo "  make harness-list                                      List supported harnesses and model coverage"
	@echo "  make harness-validate HARNESS=codex-cli PROVIDER=openai MODEL=gpt-6-luna [REASONING_EFFORT=low]"
	@echo "                                                        Check one harness/model pairing and its host credentials"
	@echo "  make codex-auth-setup [DEVICE_AUTH=1]                 Create or validate file-backed Codex ChatGPT auth"
	@echo "  make harbor-cleanup [RUN=<run-dir>] [DRY_RUN=1]        Remove trial environments a stopped run left behind"
	@echo "  make quality                                           Run repo quality gates"
	@echo ""
	@echo "Problems and runtime packs (OrbStack; DOCKER_CONTEXT defaults to orbstack):"
	@echo "  make problem-import PROBLEM=scb/forge@38d627ecf668 [SOURCE_CHECKOUT=path] [UPDATE=1]"
	@echo "                                                        Import a pinned problem (task + host-only oracle) into problems/"
	@echo "  make problem-validate PROBLEM=scb/forge@38d627ecf668 [ENGINE=direct|harbor] [NATIVE_RUNNER=path] [REPEATS=3]"
	@echo "                                                        Oracle-validate an imported problem and write its report"
	@echo "  make pack-info [RUNTIME=python]                       Show the pack image tag, lock hashes and tool provisioning state"
	@echo "  make pack-build [RUNTIME=python] [FORCE=1]            Build the agent image once per lock hash and assert no scorer tools"
	@echo "  make pack-audit [RUNTIME=python]                      Assert the built agent image has pinned harnesses, no scorer tools or browsers"
	@echo "  make pack-tool-provision TOOL=<tool> [RUNTIME=python] Provision one host-parse measurement tool into the lock-keyed cache"
	@echo "  make component-probe-trials [PROBE_EVIDENCE_DIR=<dir>] Real codex-cli/gpt-6-luna low trials proving every built-in component's discovery probe"
	@echo ""
	@echo "Experiments:"
	@echo "  make experiment-validate EXPERIMENT=examples/pilot/experiment.yaml [EXPERIMENT_SET='analysis.blocks=3']"
	@echo "                                                        Validate an experiment.yaml (schema, references, factor guard)"
	@echo "  make experiment-lock EXPERIMENT=examples/pilot/experiment.yaml [LOCK_CHECK=1]"
	@echo "                                                        Write (or check) <experiment>.lock.json with content hashes and versions"
	@echo "  make experiment-plan EXPERIMENT=... [LOCK=] [ARM=a] [BLOCK=0] [MAX_CHECKPOINTS=N] [CONCURRENCY=N] [PLAN_JSON=1]"
	@echo "                                                        Dry run: list every trial of a locked experiment with its hashes"
	@echo "  make experiment-execute EXPERIMENT=... [LOCK=] [ARM=a] [BLOCK=0] [MAX_CHECKPOINTS=N] [AGENT_OVERRIDE=oracle|nop] [CONCURRENCY=N] [RUNS_DIR=runs] [RUN_ID=]"
	@echo "                                                        Run a locked experiment through Harbor; writes attempts, validity and N1 phases"
	@echo "  make experiment-assess RUN=<run-dir> STORE=<assessment-store> SCORERS=\"correctness@1 ...\" [ASSESSMENT_CONFIG=] [REPLAY=1] [JEV_REPLAY_ONLY=1] [ASSESS_OUT=report.json]"
	@echo "                                                        Score a run's stored trials into an Assessment store (never re-runs agents)"
	@echo "  make assess-regrade RUN=<trial-or-job-dir> [ORACLE_BUNDLE=<dir>] [REGRADE_OUT=report.json]"
	@echo "                                                        Rerun the oracle over stored step snapshots; fails if results differ"
	@echo "  make experiment-analyze RUNS=\"<run-dir> ...\" STORE=<assessment-store> ANALYSIS_OUT=<dir> [LOCK=] [ASSESSMENT_CONFIG=] [MIN_PAIRS=] [MAX_EXCLUSION_FRACTION=] [ALPHA=] [BOOTSTRAP_SAMPLES=10000] [MIN_EFFECTS=\"metric=delta ...\"] [ALLOW_DEV_RUNS=1]"
	@echo "                                                        Paired deltas, CIs, curves, verdicts and sample size vs the baseline arm (thresholds from the lock; overrides are labelled)"
	@echo "  make perf-check RUN=<run-dir> [MIN_SAMPLES=10]        Fail if any N1 phase p95 exceeds its accepted budget"
	@echo "  make perf-fixture [PERF_FIXTURE_DIR=<dir>]            No-LLM fixture experiment (5 blocks x 2 arms) then perf check (Docker; builds the pack image if absent; fails, never skips, without Docker)"
	@echo "  make jev-shadow-rubric SNAPSHOTS=<checkpoint-dirs> STORE=<assessment-store> [SPECS=<steps-dir>] [WITHIN=artifacts/app] [TRIAL=<id>] [REPLAY_ONLY=1] [OUT=report.json]"
	@echo "                                                        Run the SCB rubric Jev question set in shadow; report coverage and input sufficiency"
	@echo "  make jev-export-labels POLICY=<policy> OUT=<labels.jsonl> [STORE=<assessment-store>] [LABELS=review-surface/data/jev-labels.json]"
	@echo "                                                        Export review-surface Jev labels as calibration rows (one per decision)"
	@echo "  make jev-import-labels POLICY=<policy> STORE=<assessment-store> LABELS_FILE=<labels.jsonl>"
	@echo "                                                        Import calibration labels into the store's Jev audit and show enforce readiness"
	@echo ""
	@echo "Smoke (real agent trials of examples/smoke; locks under .tmp/smoke, runs under runs/):"
	@echo "  make orchestrator-smoke [SMOKE_BLOCKS=1] [SMOKE_CONCURRENCY=1] [TIMEOUT_SEC=]"
	@echo "                                                        One one-shot trial with codex-cli + gpt-6-luna (low), correctness only"
	@echo "  make agent-smoke [HARNESS=codex-cli PROVIDER=openai MODEL=gpt-6-luna REASONING_EFFORT=low] [HARNESS_VERSION=] [TIMEOUT_SEC=]"
	@echo "                                                        The same smoke with its agent replaced (versions default to the pack pins)"
	@echo "  make smoke-matrix [SMOKE_MATRIX_SELECTOR=codex|claude] One smoke trial per pack harness (agent is the factor), or one selected arm"
	@echo "  make smoke-dry-run-check                               Print the smoke command shapes (used by make quality)"
	@echo ""
	@echo "Review surface:"
	@echo "  make benchmark-fixture-synthetic                       Write the labelled synthetic scenario-epoch fixture into experiments/benchmarks"
	@echo "  make review-fixture-synthetic [REVIEW_FIXTURE_DIR=.tmp/review-fixture]"
	@echo "                                                        Write the labelled synthetic experiment-epoch runs + Assessment store"
	@echo "  make review-surface-data [REVIEW_RUNS_ROOT=<runs-root>...] [REVIEW_STORE=<store>...] [REVIEW_LOCK=<lock>...]"
	@echo "                                                        Project experiment runs (and experiments/benchmarks history) into review-surface data"
	@echo "  make review-surface-build                              Install and build the review-surface app"
	@echo "  make review-surface-serve [REVIEW_SURFACE_PORT=5950]   Serve the review surface app and API locally"
	@echo "  make review-surface-test [REVIEW_FIXTURE=experiment|legacy] [REVIEW_RUNS_ROOT=... REVIEW_STORE=... REVIEW_LOCK=...]"
	@echo "                                                        Run the review-surface end-to-end suite (Playwright) on the synthetic fixture plus any given runs"
	@echo "  make review-surface-quality-data [QUALITY_RUNS=<runs-root> QUALITY_STORE=<assessment-store>]"
	@echo "                                                        Project Quality element data (default: labelled synthetic fixture)"

RUNTIME ?= python
TOOL ?=
FORCE ?=
PACK_DOCKER_ENV := export DOCKER_CONTEXT="$${DOCKER_CONTEXT:-orbstack}" &&

pack-info:
	@$(PACK_DOCKER_ENV) $(RAIDAR) pack info "$(RUNTIME)"

pack-build:
	@$(PACK_DOCKER_ENV) $(RAIDAR) pack build "$(RUNTIME)" $(if $(FORCE),--force,)

PROBE_EVIDENCE_DIR ?=

component-probe-trials:
	@$(PACK_DOCKER_ENV) export RAIDAR_REAL_LLM_PROBES=1 && \
		$(if $(PROBE_EVIDENCE_DIR),export RAIDAR_PROBE_EVIDENCE_DIR="$(abspath $(PROBE_EVIDENCE_DIR))" &&,) \
		cd orchestrator && $(REPO_RUNTIME_ENV) uv run --project . --extra dev \
		python -m pytest tests/test_component_real_trial_probes.py -v

pack-audit:
	@$(PACK_DOCKER_ENV) $(RAIDAR) pack audit "$(RUNTIME)"

pack-tool-provision:
	$(call require_var,TOOL)
	@$(RAIDAR) pack provision "$(RUNTIME)" "$(TOOL)"

benchmark-fixture-synthetic:
	@$(REPO_RUNTIME_ENV) uv run --project orchestrator python -m raidar.review_projection.fixture_legacy "$(CURDIR)/experiments/benchmarks"

REVIEW_FIXTURE ?= experiment
REVIEW_FIXTURE_DIR ?= $(REPO_TMP_DIR)/review-fixture
REVIEW_EXPERIMENTS_ROOT ?= $(CURDIR)/experiments/benchmarks
REVIEW_RUNS_ROOT ?=
REVIEW_STORE ?=
REVIEW_LOCK ?=

# $(1) scenario-epoch experiments roots (existing ones only), $(2) run roots, $(3) Assessment stores.
define review_projection
	@$(REPO_RUNTIME_ENV) uv run --project orchestrator python -m raidar.review_projection \
		$(foreach root,$(wildcard $(1)),--experiments-root "$(abspath $(root))") \
		$(foreach root,$(2),--runs-root "$(abspath $(root))") \
		$(foreach store,$(3),--store "$(abspath $(store))") \
		$(foreach lock,$(REVIEW_LOCK),--lock "$(abspath $(lock))") \
		--output "$(REPO_TMP_DIR)/review-projection.json"
	@RAIDAR_REVIEW_PROJECTION="$(REPO_TMP_DIR)/review-projection.json" \
		node review-surface/scripts/build-review-data.mjs
endef

review-fixture-synthetic:
	@cd orchestrator && uv run --project . python -m raidar.review_projection.fixture "$(REVIEW_FIXTURE_DIR)"

review-surface-data:
	$(call review_projection,$(REVIEW_EXPERIMENTS_ROOT),$(REVIEW_RUNS_ROOT),$(REVIEW_STORE))

review-surface-build:
	@cd review-surface && npm install --no-fund --no-audit && npm run build

review-surface-serve: review-surface-data
	@cd review-surface && node server.mjs

QUALITY_FIXTURE_DIR ?= $(REPO_TMP_DIR)/quality-fixture

review-surface-quality-data:
	$(if $(QUALITY_RUNS),$(if $(QUALITY_STORE),,$(error QUALITY_STORE is required with QUALITY_RUNS)))
	@$(if $(QUALITY_RUNS),true,$(REPO_RUNTIME_ENV) uv run --project orchestrator python -m raidar.review_quality.synthetic "$(QUALITY_FIXTURE_DIR)")
	@$(REPO_RUNTIME_ENV) uv run --project orchestrator python -m raidar.review_quality \
		--runs-root "$(or $(QUALITY_RUNS),$(QUALITY_FIXTURE_DIR)/runs)" \
		--store "$(or $(QUALITY_STORE),$(QUALITY_FIXTURE_DIR)/store)" \
		--output "$(REPO_TMP_DIR)/quality-projection.json" $(if $(QUALITY_RUNS),,--synthetic)
	@RAIDAR_QUALITY_PROJECTION="$(REPO_TMP_DIR)/quality-projection.json" \
		node review-surface/scripts/build-quality-data.mjs

# The experiment fixture replaces the scenario-epoch synthetic fixture (same run ids) and
# carries its own scenario-epoch experiments ($(REVIEW_FIXTURE_DIR)/experiments), so the old
# epoch always renders; experiments/benchmarks joins only when REVIEW_EXPERIMENTS_ROOT is given
# explicitly.
REVIEW_TEST_EXPERIMENTS_ROOT = $(REVIEW_FIXTURE_DIR)/experiments $(if $(filter command line,$(origin REVIEW_EXPERIMENTS_ROOT)),$(REVIEW_EXPERIMENTS_ROOT),)

review-surface-test: $(if $(filter legacy,$(REVIEW_FIXTURE)),benchmark-fixture-synthetic,review-fixture-synthetic) review-surface-quality-data
ifeq ($(REVIEW_FIXTURE),legacy)
	$(call review_projection,$(REVIEW_EXPERIMENTS_ROOT),$(REVIEW_RUNS_ROOT),$(REVIEW_STORE))
else
	$(call review_projection,$(REVIEW_TEST_EXPERIMENTS_ROOT),$(REVIEW_FIXTURE_DIR)/runs $(REVIEW_RUNS_ROOT),$(REVIEW_FIXTURE_DIR)/store $(REVIEW_STORE))
endif
	@cd review-surface && npm install --no-fund --no-audit && npm run build \
		&& npx playwright install chromium && npm test

env-setup:
	@$(RAIDAR_DEV) env setup --sync-arg --extra --sync-arg dev

harness-list:
	@$(RAIDAR) harness list

harness-validate:
	$(call require_var,HARNESS)
	$(call require_var,PROVIDER)
	$(call require_var,MODEL)
	@$(RAIDAR) harness validate \
		--harness "$(HARNESS)" \
		--provider "$(PROVIDER)" \
		--model "$(MODEL)" \
		$(if $(REASONING_EFFORT),--reasoning-effort "$(REASONING_EFFORT)",) \
		$(if $(TIMEOUT_SEC),--timeout "$(TIMEOUT_SEC)",)

codex-auth-setup:
	@$(RAIDAR) harness setup-auth \
		--harness "codex-cli" \
		$(if $(filter 1 true yes on,$(DEVICE_AUTH)),--device-auth,)

harbor-cleanup:
	@$(PACK_DOCKER_ENV) $(RAIDAR) harbor cleanup $(if $(RUN),--run "$(RUN)",) $(if $(DRY_RUN),--dry-run,)

docker-check:
	@$(PACK_DOCKER_ENV) if ! docker info >/dev/null 2>&1; then \
		echo "Docker (context $${DOCKER_CONTEXT}) is required for smoke workflows that use Harbor. Start it and retry."; \
		exit 1; \
	fi

# Scenario-era targets removed at the WS-L cutover (scenarios/ and matrices/ are read-only
# history). Each refuses with a pointer to the experiment workflow.
REMOVED_SCENARIO_TARGETS := scenario-list scenario-init scenario-clone-revision scenario-info \
	scenario-validate experiment-run matrix-run runtime-stack-scenario-smoke \
	experiments-list experiments-prune

.PHONY: $(REMOVED_SCENARIO_TARGETS)
$(REMOVED_SCENARIO_TARGETS):
	@echo "Refusing $@: the scenario/matrix workflow was removed at the reboot cutover (WS-L)." >&2
	@echo "scenarios/ and matrices/ are read-only history. Author an experiment.yaml instead:" >&2
	@echo "  make experiment-validate / experiment-lock / experiment-execute (see README.md and make help)." >&2
	@exit 1

experiment-validate:
	$(call require_var,EXPERIMENT)
	@$(RAIDAR) validate "$(EXPERIMENT)" $(foreach item,$(EXPERIMENT_SET),--set "$(item)")

experiment-lock:
	$(call require_var,EXPERIMENT)
	@$(RAIDAR) lock "$(EXPERIMENT)" $(foreach item,$(EXPERIMENT_SET),--set "$(item)") $(if $(LOCK_CHECK),--check,)

EXECUTION_SELECT = $(if $(LOCK),--lock "$(LOCK)",) $(foreach arm,$(ARM),--arm "$(arm)") \
	$(foreach block,$(BLOCK),--block "$(block)") \
	$(if $(MAX_CHECKPOINTS),--max-checkpoints "$(MAX_CHECKPOINTS)",) \
	$(if $(AGENT_OVERRIDE),--agent-override "$(AGENT_OVERRIDE)",) \
	$(if $(CONCURRENCY),--concurrency "$(CONCURRENCY)",) \
	$(foreach item,$(EXPERIMENT_SET),--set "$(item)")

experiment-plan:
	$(call require_var,EXPERIMENT)
	@$(RAIDAR) plan "$(EXPERIMENT)" $(EXECUTION_SELECT) $(if $(PLAN_JSON),--json,)

experiment-execute:
	$(call require_var,EXPERIMENT)
	@$(PACK_DOCKER_ENV) $(RAIDAR) run "$(EXPERIMENT)" $(EXECUTION_SELECT) \
		--runs-dir "$(if $(RUNS_DIR),$(abspath $(RUNS_DIR)),$(CURDIR)/runs)" $(if $(RUN_ID),--run-id "$(RUN_ID)",)

perf-check:
	$(call require_var,RUN)
	@$(RAIDAR) perf check $(RUN) $(if $(MIN_SAMPLES),--min-samples "$(MIN_SAMPLES)",)

PERF_FIXTURE_DIR ?=

perf-fixture:
	@$(PACK_DOCKER_ENV) $(RAIDAR) pack build python
	@$(PACK_DOCKER_ENV) export RAIDAR_EXECUTION_DOCKER=1 && \
		$(if $(PERF_FIXTURE_DIR),export RAIDAR_PERF_FIXTURE_DIR="$(abspath $(PERF_FIXTURE_DIR))" &&,) \
		cd orchestrator && $(REPO_RUNTIME_ENV) uv run --project . --extra dev \
		python -m pytest tests/test_execution_perf_fixture.py -v -s -rs

problem-import:
	$(call require_var,PROBLEM)
	@$(RAIDAR) problem import $(PROBLEM) $(if $(SOURCE_CHECKOUT),--source-checkout "$(SOURCE_CHECKOUT)",) $(if $(UPDATE),--update,)

problem-validate:
	$(call require_var,PROBLEM)
	@$(PACK_DOCKER_ENV) $(RAIDAR) problem validate "$(PROBLEM)" \
		$(if $(ENGINE),--engine "$(ENGINE)",) $(if $(REPEATS),--repeats "$(REPEATS)",) \
		$(if $(NATIVE_RUNNER),--native-runner "$(NATIVE_RUNNER)",) \
		$(if $(SOURCE_CHECKOUT),--source-checkout "$(SOURCE_CHECKOUT)",) \
		$(if $(PROBLEM_STORE),--store "$(PROBLEM_STORE)",)

RUN ?=
ORACLE_BUNDLE ?=
REGRADE_OUT ?=

experiment-assess:
	$(call require_var,RUN)
	$(call require_var,STORE)
	$(call require_var,SCORERS)
	@$(PACK_DOCKER_ENV) $(RAIDAR) assess --rescore "$(if $(wildcard $(RUN)/trials),$(RUN)/trials,$(RUN))" --store "$(STORE)" \
		$(foreach scorer,$(SCORERS),--scorer "$(scorer)") \
		$(if $(ASSESSMENT_CONFIG),--config "$(ASSESSMENT_CONFIG)",) $(if $(REPLAY),--replay,) \
		$(if $(JEV_REPLAY_ONLY),--jev-replay-only,) $(if $(ASSESS_OUT),--out "$(ASSESS_OUT)",)

assess-regrade:
	$(call require_var,RUN)
	@export DOCKER_CONTEXT="$${DOCKER_CONTEXT:-orbstack}" && $(RAIDAR) assess --regrade "$(RUN)" --docker-context "$${DOCKER_CONTEXT}" $(if $(ORACLE_BUNDLE),--bundle "$(ORACLE_BUNDLE)",) $(if $(REGRADE_OUT),--out "$(REGRADE_OUT)",)

experiment-analyze:
	$(call require_var,RUNS)
	$(call require_var,STORE)
	$(call require_var,ANALYSIS_OUT)
	@$(RAIDAR) analyze $(foreach run,$(RUNS),"$(run)") --store "$(STORE)" --output "$(ANALYSIS_OUT)" $(if $(LOCK),--lock "$(LOCK)",) $(if $(ASSESSMENT_CONFIG),--assessment-config "$(ASSESSMENT_CONFIG)",) $(if $(MIN_PAIRS),--min-pairs "$(MIN_PAIRS)",) $(if $(MAX_EXCLUSION_FRACTION),--max-exclusion-fraction "$(MAX_EXCLUSION_FRACTION)",) $(if $(ALPHA),--alpha "$(ALPHA)",) $(if $(BOOTSTRAP_SAMPLES),--bootstrap-samples "$(BOOTSTRAP_SAMPLES)",) $(foreach effect,$(MIN_EFFECTS),--min-effect "$(effect)") $(if $(ALLOW_DEV_RUNS),--allow-dev-runs,)

jev-export-labels:
	$(call require_var,POLICY)
	$(call require_var,OUT)
	@$(RAIDAR) jev export-labels --labels "$(or $(LABELS),$(CURDIR)/review-surface/data/jev-labels.json)" --policy "$(POLICY)" $(if $(STORE),--store "$(STORE)",) --output "$(OUT)"

jev-import-labels:
	$(call require_var,POLICY)
	$(call require_var,STORE)
	$(call require_var,LABELS_FILE)
	@$(RAIDAR) jev import-labels --store "$(STORE)" --policy "$(POLICY)" "$(LABELS_FILE)"

jev-shadow-rubric:
	$(call require_var,SNAPSHOTS)
	$(call require_var,STORE)
	@$(RAIDAR) jev shadow-rubric --snapshots "$(SNAPSHOTS)" --store "$(STORE)" $(if $(SPECS),--specs "$(SPECS)",) $(if $(REPLAY_ONLY),--replay-only,) $(if $(WITHIN),--within "$(WITHIN)",) $(if $(TRIAL),--trial "$(TRIAL)",) $(if $(OUT),--output "$(OUT)",)

# $(1) experiment file, $(2) lock name under SMOKE_LOCK_DIR, $(3) spec overrides (--set),
# $(4) trial selection (--arm). The lock and the run see the same overrides.
define smoke_run
	@mkdir -p "$(SMOKE_LOCK_DIR)"
	@$(RAIDAR) lock "$(1)" --output "$(SMOKE_LOCK_DIR)/$(2).lock.json" $(3)
	@$(PACK_DOCKER_ENV) $(RAIDAR) run "$(1)" --lock "$(SMOKE_LOCK_DIR)/$(2).lock.json" $(3) $(4) \
		--concurrency "$(SMOKE_CONCURRENCY)" --runs-dir "$(SMOKE_RUNS_DIR)"
endef

SMOKE_SETS = --set "analysis.blocks=$(SMOKE_BLOCKS)" \
	$(if $(TIMEOUT_SEC),--set "protocol.agent_timeout_sec=$(TIMEOUT_SEC)",)
AGENT_SMOKE_SETS = $(SMOKE_SETS) \
	--set "agents.0.harness=$(AGENT_SMOKE_EFFECTIVE_HARNESS)" \
	--set "agents.0.provider=$(AGENT_SMOKE_EFFECTIVE_PROVIDER)" \
	--set "agents.0.model=$(AGENT_SMOKE_EFFECTIVE_MODEL)" \
	--set "agents.0.reasoning_effort=$(AGENT_SMOKE_EFFECTIVE_REASONING)" \
	--set "agents.0.version='$(AGENT_SMOKE_EFFECTIVE_VERSION)'"

smoke-dry-run-check:
	@$(MAKE) --no-print-directory -n orchestrator-smoke SMOKE_BLOCKS="2" SMOKE_CONCURRENCY="2"
	@$(MAKE) --no-print-directory -n smoke-matrix
	@$(MAKE) --no-print-directory -n smoke-matrix SMOKE_MATRIX_SELECTOR="codex"
	@$(MAKE) --no-print-directory -n smoke-matrix SMOKE_MATRIX_SELECTOR="claude"
	@! $(MAKE) --no-print-directory -n smoke-matrix SMOKE_MATRIX_SELECTOR="gemini" \
		>/dev/null 2>&1
	@! $(MAKE) --no-print-directory -n smoke-matrix SMOKE_MATRIX_SELECTOR="unknown" \
		>/dev/null 2>&1
	@$(MAKE) --no-print-directory -n agent-smoke \
		HARNESS="codex-cli" PROVIDER="openai" MODEL="gpt-6-luna" REASONING_EFFORT="low" \
		TIMEOUT_SEC="300"

orchestrator-smoke: docker-check
	$(call smoke_run,$(SMOKE_EXPERIMENT),orchestrator-smoke,$(SMOKE_SETS),)

agent-smoke: docker-check
	@if [ -z "$(AGENT_SMOKE_EFFECTIVE_VERSION)" ]; then \
		echo "No pack pin for harness '$(AGENT_SMOKE_EFFECTIVE_HARNESS)'; set HARNESS_VERSION"; \
		exit 1; \
	fi
	$(call smoke_run,$(SMOKE_EXPERIMENT),agent-smoke,$(AGENT_SMOKE_SETS),)

smoke-matrix: docker-check
	$(call smoke_run,$(SMOKE_AGENTS_EXPERIMENT),smoke-matrix,$(SMOKE_SETS),$(if $(SMOKE_MATRIX_SELECTOR),--arm "$(SMOKE_MATRIX_SELECTOR)",))

quality:
	@$(MAKE) --no-print-directory smoke-dry-run-check
	@$(RAIDAR_DEV) quality gates
	@cd orchestrator && uv run --project . --extra dev python -m lizard -C 10 -l python src
