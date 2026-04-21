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

# Shared public workflow defaults.
SCENARIO ?=
SCENARIO_DIR ?=
SCENARIO_REVISION ?=
NAME ?=
HARNESS ?=
PROVIDER ?=
MODEL ?=
DEVICE_AUTH ?=
RUN_COUNT ?= 5
RUN_PARALLELISM ?= 1
RERUN_UNSCORED ?= 0
EVALUATION_PROFILE ?=
LIMIT ?=
KEEP_PER_MODEL ?=
EXPERIMENT_KIND ?= benchmark
STARTER_ROOT ?=
PROMPT_ENTRY ?=
DIFFICULTY ?=
CATEGORY ?=
TIMEOUT_SEC ?=

# Canonical smoke workflow defaults.
ORCHESTRATOR_SMOKE_SCENARIO := scenarios/hello-world-smoke/v001/scenario.yaml
ORCHESTRATOR_SMOKE_HARNESS := codex-cli
ORCHESTRATOR_SMOKE_PROVIDER := openai
ORCHESTRATOR_SMOKE_MODEL := gpt-5.4-mini
ORCHESTRATOR_SMOKE_REPEATS ?= 1
SMOKE_MATRIX_SCENARIO ?= $(ORCHESTRATOR_SMOKE_SCENARIO)
SMOKE_MATRIX_SELECTOR ?= all
SMOKE_MATRIX_REPEATS ?= 1
SMOKE_MATRIX_REPEAT_PARALLEL ?= 1
SMOKE_MATRIX_RERUN_UNSCORED ?= 0
AGENT_SMOKE_SCENARIO ?= $(ORCHESTRATOR_SMOKE_SCENARIO)
AGENT_SMOKE_REPEATS ?= 1
AGENT_SMOKE_REPEAT_PARALLEL ?= 1
AGENT_SMOKE_RERUN_UNSCORED ?= 0

ifeq ($(firstword $(MAKECMDGOALS)),matrix-run)
MATRIX_ARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
$(foreach goal,$(MATRIX_ARGS),$(eval $(goal):;@:))
endif

.PHONY: help \
	env-setup harness-list harness-validate codex-auth-setup harbor-cleanup docker-check scenario-list scenario-init scenario-clone-revision scenario-info scenario-validate \
	smoke-dry-run-check orchestrator-smoke smoke-matrix agent-smoke \
	experiment-run matrix-run \
	experiments-list experiments-prune \
	quality

define require_var
	@if [ -z "$($1)" ]; then \
		echo "Missing required variable: $1"; \
		exit 1; \
	fi
endef

help:
	@echo "Public workflow surface"
	@echo ""
	@echo "Environment and validation:"
	@echo "  make env-setup                                         Bootstrap the orchestrator environment"
	@echo "  make harness-list                                      List supported harnesses and model coverage"
	@echo "  make harness-validate HARNESS=codex-cli PROVIDER=openai MODEL=gpt-5.4-mini"
	@echo "                                                        Validate one AgentSpec candidate"
	@echo "  make codex-auth-setup [DEVICE_AUTH=1]                 Create or validate file-backed Codex ChatGPT auth"
	@echo "  make harbor-cleanup                                    Cleanup stale Harbor processes and containers"
	@echo "  make scenario-list                                     List available scenarios and revisions"
	@echo "  make scenario-init SCENARIO_DIR=scenarios/new-scenario SCENARIO_REVISION=v001"
	@echo "                                                        Scaffold a new scenario"
	@echo "  make scenario-clone-revision SCENARIO_DIR=scenarios/homepage-implementation FROM_REVISION=v001 [TO_REVISION=v002]"
	@echo "                                                        Create a new revision inside an existing scenario root"
	@echo "  make scenario-info SCENARIO_DIR=scenarios/homepage-implementation [SCENARIO_REVISION=v001]"
	@echo "                                                        Inspect a scenario and show revision yaml paths"
	@echo "  make scenario-validate SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml"
	@echo "                                                        Validate a scenario contract"
	@echo "  make quality                                           Run repo quality gates"
	@echo ""
	@echo "Experiment orchestration:"
	@echo "  make smoke-dry-run-check                               Print the canonical smoke command shapes used by CI drift checks"
	@echo "  make orchestrator-smoke                                Run the default orchestrator smoke scenario on codex-cli with openai/gpt-5.4-mini"
	@echo "                                                        Override ORCHESTRATOR_SMOKE_REPEATS and RUN_PARALLELISM for repeat smoke"
	@echo "  make smoke-matrix                                      Run the default hello-world smoke scenario across the full public model matrix"
	@echo "  make agent-smoke HARNESS=codex-cli PROVIDER=openai MODEL=gpt-5.4-mini"
	@echo "                                                        Run the canonical agent smoke workflow via public make targets"
	@echo "  make experiment-run SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml HARNESS=... PROVIDER=... MODEL=..."
	@echo "                                                        Run one scenario yaml for one AgentSpec"
	@echo "  make matrix-run scenarios/homepage-implementation/v001/scenario.yaml all"
	@echo "                                                        Run a generated matrix for all benchmark model sets"
	@echo "  make matrix-run scenarios/homepage-implementation/v001/scenario.yaml codex"
	@echo "                                                        Run a generated matrix for one provider family"
	@echo "  make experiments-list [EVALUATION_PROFILE=...] [LIMIT=...]"
	@echo "                                                        List stored experiments and summaries"
	@echo "  make experiments-prune [KEEP_PER_MODEL=1]"
	@echo "                                                        Preview artifact pruning decisions"

env-setup:
	@$(RAIDAR) env setup

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
		$(if $(TIMEOUT_SEC),--timeout "$(TIMEOUT_SEC)",)

codex-auth-setup:
	@$(RAIDAR) harness setup-auth \
		--harness "codex-cli" \
		$(if $(filter 1 true yes on,$(DEVICE_AUTH)),--device-auth,)

harbor-cleanup:
	@$(RAIDAR) harbor cleanup

docker-check:
	@if ! docker info >/dev/null 2>&1; then \
		echo "Docker daemon is required for smoke workflows that use Harbor. Start Docker and retry."; \
		exit 1; \
	fi

scenario-list:
	@$(RAIDAR) scenario list

scenario-init:
	$(call require_var,SCENARIO_DIR)
	@$(RAIDAR) scenario init \
		--path "$(SCENARIO_DIR)" \
		$(if $(NAME),--name "$(NAME)",) \
		$(if $(SCENARIO_REVISION),--scenario-revision "$(SCENARIO_REVISION)",) \
		$(if $(STARTER_ROOT),--starter-root "$(STARTER_ROOT)",) \
		$(if $(PROMPT_ENTRY),--prompt-entry "$(PROMPT_ENTRY)",) \
		$(if $(DIFFICULTY),--difficulty "$(DIFFICULTY)",) \
		$(if $(CATEGORY),--category "$(CATEGORY)",) \
		$(if $(TIMEOUT_SEC),--timeout "$(TIMEOUT_SEC)",)

scenario-clone-revision:
	$(call require_var,SCENARIO_DIR)
	$(call require_var,FROM_REVISION)
	@$(RAIDAR) scenario clone-revision \
		--path "$(SCENARIO_DIR)" \
		--from-revision "$(FROM_REVISION)" \
		$(if $(TO_REVISION),--to-revision "$(TO_REVISION)",)

scenario-info:
	$(call require_var,SCENARIO_DIR)
	@$(RAIDAR) info --scenario "$(if $(SCENARIO_REVISION),$(SCENARIO_DIR)/$(SCENARIO_REVISION),$(SCENARIO_DIR))"

scenario-validate:
	$(call require_var,SCENARIO)
	@$(RAIDAR) scenario validate --scenario "$(SCENARIO)"

smoke-dry-run-check:
	@$(MAKE) --no-print-directory -n orchestrator-smoke \
		ORCHESTRATOR_SMOKE_REPEATS="2" \
		RUN_PARALLELISM="2"
	@$(MAKE) --no-print-directory -n smoke-matrix \
		SMOKE_MATRIX_REPEATS="1" \
		SMOKE_MATRIX_REPEAT_PARALLEL="1"
	@$(MAKE) --no-print-directory -n agent-smoke \
		HARNESS="$(ORCHESTRATOR_SMOKE_HARNESS)" \
		PROVIDER="$(ORCHESTRATOR_SMOKE_PROVIDER)" \
		MODEL="$(ORCHESTRATOR_SMOKE_MODEL)" \
		AGENT_SMOKE_REPEATS="2" \
		AGENT_SMOKE_REPEAT_PARALLEL="2"

orchestrator-smoke smoke-matrix agent-smoke: export HARBOR_SMOKE_FAST := 1
orchestrator-smoke smoke-matrix agent-smoke: export HARBOR_SMOKE_FAST_REUSE_IMAGE := 1

orchestrator-smoke: docker-check
	@$(RAIDAR) run \
		--scenario "$(ORCHESTRATOR_SMOKE_SCENARIO)" \
		--harness "$(ORCHESTRATOR_SMOKE_HARNESS)" \
		--provider "$(ORCHESTRATOR_SMOKE_PROVIDER)" \
		--model "$(ORCHESTRATOR_SMOKE_MODEL)" \
		--repeats "$(ORCHESTRATOR_SMOKE_REPEATS)" \
		--repeat-parallel "$(RUN_PARALLELISM)" \
		--rerun-unscored "$(RERUN_UNSCORED)" \
		--experiment-kind "$(EXPERIMENT_KIND)" \
		$(if $(TIMEOUT_SEC),--timeout "$(TIMEOUT_SEC)",)

smoke-matrix: docker-check
	@$(RAIDAR) matrix \
		--scenario "$(SMOKE_MATRIX_SCENARIO)" \
		--selector "$(SMOKE_MATRIX_SELECTOR)" \
		--repeats "$(SMOKE_MATRIX_REPEATS)" \
		--repeat-parallel "$(SMOKE_MATRIX_REPEAT_PARALLEL)" \
		--rerun-unscored "$(SMOKE_MATRIX_RERUN_UNSCORED)" \
		--experiment-kind "$(EXPERIMENT_KIND)" \
		$(if $(TIMEOUT_SEC),--timeout "$(TIMEOUT_SEC)",)

agent-smoke: docker-check
	$(call require_var,HARNESS)
	$(call require_var,PROVIDER)
	$(call require_var,MODEL)
	@$(MAKE) harbor-cleanup
	@$(MAKE) harness-validate \
		HARNESS="$(HARNESS)" \
		PROVIDER="$(PROVIDER)" \
		MODEL="$(MODEL)" \
		$(if $(filter codex-cli,$(HARNESS)),CODEX_AUTH_MODE="chatgpt",) \
		$(if $(TIMEOUT_SEC),TIMEOUT_SEC="$(TIMEOUT_SEC)",)
	@$(MAKE) experiment-run \
		SCENARIO="$(AGENT_SMOKE_SCENARIO)" \
		HARNESS="$(HARNESS)" \
		PROVIDER="$(PROVIDER)" \
		MODEL="$(MODEL)" \
		$(if $(filter codex-cli,$(HARNESS)),CODEX_AUTH_MODE="chatgpt",) \
		RUN_COUNT="$(AGENT_SMOKE_REPEATS)" \
		RUN_PARALLELISM="$(AGENT_SMOKE_REPEAT_PARALLEL)" \
		RERUN_UNSCORED="$(AGENT_SMOKE_RERUN_UNSCORED)" \
		EXPERIMENT_KIND="$(EXPERIMENT_KIND)" \
		$(if $(TIMEOUT_SEC),TIMEOUT_SEC="$(TIMEOUT_SEC)",)

experiment-run:
	$(call require_var,SCENARIO)
	$(call require_var,HARNESS)
	$(call require_var,PROVIDER)
	$(call require_var,MODEL)
	@$(RAIDAR) experiment run \
		--scenario "$(SCENARIO)" \
		--harness "$(HARNESS)" \
		--provider "$(PROVIDER)" \
		--model "$(MODEL)" \
		--repeats "$(RUN_COUNT)" \
		--repeat-parallel "$(RUN_PARALLELISM)" \
		--rerun-unscored "$(RERUN_UNSCORED)" \
		--experiment-kind "$(EXPERIMENT_KIND)" \
		$(if $(TIMEOUT_SEC),--timeout "$(TIMEOUT_SEC)",)

matrix-run:
	@if [ "$(words $(MATRIX_ARGS))" -ne 2 ]; then \
		echo "Usage: make matrix-run <scenario-yaml> <all|codex|gemini|claude>"; \
		exit 1; \
	fi
	@$(RAIDAR) matrix \
		--scenario "$(word 1,$(MATRIX_ARGS))" \
		--selector "$(word 2,$(MATRIX_ARGS))" \
		--repeats "$(RUN_COUNT)" \
		--repeat-parallel "$(RUN_PARALLELISM)" \
		--rerun-unscored "$(RERUN_UNSCORED)" \
		--experiment-kind "$(EXPERIMENT_KIND)" \
		$(if $(TIMEOUT_SEC),--timeout "$(TIMEOUT_SEC)",)

experiments-list:
	@$(RAIDAR) experiments list \
		--experiment-kind "$(EXPERIMENT_KIND)" \
		$(if $(EVALUATION_PROFILE),--evaluation-profile "$(EVALUATION_PROFILE)",) \
		$(if $(LIMIT),--limit "$(LIMIT)",)

experiments-prune:
	@$(RAIDAR) experiments prune --dry-run \
		--experiment-kind "$(EXPERIMENT_KIND)" \
		$(if $(KEEP_PER_MODEL),--keep-per-model "$(KEEP_PER_MODEL)",)

quality:
	@$(MAKE) --no-print-directory smoke-dry-run-check
	@$(RAIDAR) quality gates
	@cd orchestrator && uv run --project . --extra dev python -m lizard -C 10 -l python src
