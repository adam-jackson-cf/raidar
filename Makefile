SHELL := /bin/bash

RAIDAR := uv run --project orchestrator raidar
AUTO_RESEARCHER := uv run --project auto_researcher auto-researcher

# Shared public workflow defaults.
SCENARIO ?=
SCENARIO_DIR ?=
SCENARIO_REVISION ?=
NAME ?=
HARNESS ?=
MODEL ?=
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
GOAL ?=
OBJECTIVE_ID ?=
TARGET_HARNESS ?=
TARGET_MODEL ?=
MAX_REVISIONS ?= 3
LOOP_EXECUTION_MODE ?= serial
MAX_PARALLEL_LOOPS ?= 3
BENCHMARK_REPEATS ?= 5
BENCHMARK_REPEAT_PARALLEL ?= 1
RESEARCH_REPEATS ?= 3
RESEARCH_REPEAT_PARALLEL ?= 1
CONTROL_PROVIDER ?= openai-codex
CONTROL_MODEL ?= gpt-5.3-codex
PI_BINARY ?= pi

# Canonical smoke workflow defaults.
ORCHESTRATOR_SMOKE_SCENARIO := scenarios/hello-world-smoke/v001/scenario.yaml
ORCHESTRATOR_SMOKE_HARNESS := codex-cli
ORCHESTRATOR_SMOKE_MODEL := codex/gpt-5.4-mini
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
RESEARCH_SMOKE_GOAL ?= Draft and approve a minimal hello-world coding scenario for autoresearch smoke validation
RESEARCH_SMOKE_TARGET_HARNESS ?= codex-cli
RESEARCH_SMOKE_TARGET_MODEL ?= codex/gpt-5.4-mini
RESEARCH_SMOKE_CONTROL_PROVIDER ?= openai-codex
RESEARCH_SMOKE_CONTROL_MODEL ?= gpt-5.3-codex
RESEARCH_SMOKE_LOOP_EXECUTION_MODE ?= serial
RESEARCH_SMOKE_MAX_REVISIONS ?= 1
RESEARCH_SMOKE_MAX_PARALLEL_LOOPS ?= 1
RESEARCH_SMOKE_BENCHMARK_REPEATS ?= 1
RESEARCH_SMOKE_BENCHMARK_REPEAT_PARALLEL ?= 1
RESEARCH_SMOKE_RESEARCH_REPEATS ?= 1
RESEARCH_SMOKE_RESEARCH_REPEAT_PARALLEL ?= 1
RESEARCH_SMOKE_OBJECTIVE_ID ?= research-smoke-$(shell uuidgen | tr '[:upper:]' '[:lower:]')
RESEARCH_SMOKE_OBJECTIVE_ROOT ?= $(CURDIR)/auto_researcher/objectives/$(RESEARCH_SMOKE_OBJECTIVE_ID)
RESEARCH_SMOKE_STATE_PATH ?= $(RESEARCH_SMOKE_OBJECTIVE_ROOT)/objective.yaml

ifeq ($(firstword $(MAKECMDGOALS)),matrix-run)
MATRIX_ARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
$(foreach goal,$(MATRIX_ARGS),$(eval $(goal):;@:))
endif

.PHONY: help \
	env-setup harness-list harness-validate harbor-cleanup docker-check scenario-list scenario-init scenario-info scenario-validate \
	smoke-dry-run-check orchestrator-smoke smoke-matrix agent-smoke research-smoke \
	research-smoke-init research-smoke-approve research-smoke-cleanup \
	experiment-run matrix-run \
	experiments-list experiments-prune \
	auto-research-init auto-research-approve-scenario auto-research-run auto-research-status auto-research-report \
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
	@echo "  make harness-validate HARNESS=codex-cli MODEL=codex/gpt-5.4-mini"
	@echo "                                                        Validate one AgentSpec candidate"
	@echo "  make harbor-cleanup                                    Cleanup stale Harbor processes and containers"
	@echo "  make scenario-list                                     List available scenarios and revisions"
	@echo "  make scenario-init SCENARIO_DIR=scenarios/new-scenario SCENARIO_REVISION=v001"
	@echo "                                                        Scaffold a new scenario"
	@echo "  make scenario-info SCENARIO_DIR=scenarios/homepage-implementation [SCENARIO_REVISION=v001]"
	@echo "                                                        Inspect a scenario and show revision yaml paths"
	@echo "  make scenario-validate SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml"
	@echo "                                                        Validate a scenario contract"
	@echo "  make quality                                           Run repo quality gates"
	@echo ""
	@echo "Experiment orchestration:"
	@echo "  make smoke-dry-run-check                               Print the canonical smoke command shapes used by CI drift checks"
	@echo "  make orchestrator-smoke                                Run the default orchestrator smoke scenario on codex-cli with codex/gpt-5.4-mini"
	@echo "                                                        Override ORCHESTRATOR_SMOKE_REPEATS and RUN_PARALLELISM for repeat smoke"
	@echo "  make smoke-matrix                                      Run the default hello-world smoke scenario across the full public model matrix"
	@echo "  make agent-smoke HARNESS=codex-cli MODEL=codex/gpt-5.4-mini"
	@echo "                                                        Run the canonical agent smoke workflow via public make targets"
	@echo "  make research-smoke                                    Run canonical autoresearch init+approve and clean up smoke artifacts"
	@echo "  make experiment-run SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml HARNESS=... MODEL=..."
	@echo "                                                        Run one scenario yaml for one AgentSpec"
	@echo "  make matrix-run scenarios/homepage-implementation/v001/scenario.yaml all"
	@echo "                                                        Run a generated matrix for all benchmark model sets"
	@echo "  make matrix-run scenarios/homepage-implementation/v001/scenario.yaml codex"
	@echo "                                                        Run a generated matrix for one provider family"
	@echo "  make experiments-list [EVALUATION_PROFILE=...] [LIMIT=...]"
	@echo "                                                        List stored experiments and summaries"
	@echo "  make experiments-prune [KEEP_PER_MODEL=1]"
	@echo "                                                        Preview artifact pruning decisions"
	@echo "  make auto-research-init GOAL='...' TARGET_HARNESS=... TARGET_MODEL=..."
	@echo "                                                        Draft an objective-scoped scenario under auto_researcher/"
	@echo "  make auto-research-approve-scenario OBJECTIVE_ID=..."
	@echo "                                                        Promote an approved draft scenario and seed the first benchmark"
	@echo "  make auto-research-run OBJECTIVE_ID=..."
	@echo "                                                        Execute bounded research loops for an approved objective"
	@echo "  make auto-research-status OBJECTIVE_ID=..."
	@echo "                                                        Show current objective and loop state"
	@echo "  make auto-research-report OBJECTIVE_ID=..."
	@echo "                                                        Print the current objective report"

env-setup:
	@$(RAIDAR) env setup

harness-list:
	@$(RAIDAR) harness list

harness-validate:
	$(call require_var,HARNESS)
	$(call require_var,MODEL)
	@$(RAIDAR) harness validate \
		--harness "$(HARNESS)" \
		--model "$(MODEL)" \
		$(if $(TIMEOUT_SEC),--timeout "$(TIMEOUT_SEC)",)

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
		MODEL="$(ORCHESTRATOR_SMOKE_MODEL)" \
		AGENT_SMOKE_REPEATS="2" \
		AGENT_SMOKE_REPEAT_PARALLEL="2"
	@$(MAKE) --no-print-directory -n research-smoke \
		RESEARCH_SMOKE_OBJECTIVE_ID="research-smoke-dry-run" \
		RESEARCH_SMOKE_LOOP_EXECUTION_MODE="parallel" \
		RESEARCH_SMOKE_MAX_PARALLEL_LOOPS="2" \
		RESEARCH_SMOKE_BENCHMARK_REPEATS="2" \
		RESEARCH_SMOKE_BENCHMARK_REPEAT_PARALLEL="2" \
		RESEARCH_SMOKE_RESEARCH_REPEATS="2" \
		RESEARCH_SMOKE_RESEARCH_REPEAT_PARALLEL="2"

orchestrator-smoke: docker-check
	@$(RAIDAR) run \
		--scenario "$(ORCHESTRATOR_SMOKE_SCENARIO)" \
		--harness "$(ORCHESTRATOR_SMOKE_HARNESS)" \
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
	$(call require_var,MODEL)
	@$(MAKE) harbor-cleanup
	@$(MAKE) harness-validate \
		HARNESS="$(HARNESS)" \
		MODEL="$(MODEL)" \
		$(if $(TIMEOUT_SEC),TIMEOUT_SEC="$(TIMEOUT_SEC)",)
	@$(MAKE) experiment-run \
		SCENARIO="$(AGENT_SMOKE_SCENARIO)" \
		HARNESS="$(HARNESS)" \
		MODEL="$(MODEL)" \
		RUN_COUNT="$(AGENT_SMOKE_REPEATS)" \
		RUN_PARALLELISM="$(AGENT_SMOKE_REPEAT_PARALLEL)" \
		RERUN_UNSCORED="$(AGENT_SMOKE_RERUN_UNSCORED)" \
		EXPERIMENT_KIND="$(EXPERIMENT_KIND)" \
		$(if $(TIMEOUT_SEC),TIMEOUT_SEC="$(TIMEOUT_SEC)",)

research-smoke: docker-check
	@status=0; cleanup_status=0; \
		$(MAKE) --no-print-directory research-smoke-init \
			RESEARCH_SMOKE_OBJECTIVE_ID="$(RESEARCH_SMOKE_OBJECTIVE_ID)" || status=$$?; \
		if [ "$$status" -eq 0 ]; then \
			$(MAKE) --no-print-directory research-smoke-approve \
				RESEARCH_SMOKE_OBJECTIVE_ID="$(RESEARCH_SMOKE_OBJECTIVE_ID)" || status=$$?; \
		fi; \
		$(MAKE) --no-print-directory research-smoke-cleanup \
			RESEARCH_SMOKE_OBJECTIVE_ID="$(RESEARCH_SMOKE_OBJECTIVE_ID)" || cleanup_status=$$?; \
		if [ "$$status" -ne 0 ]; then \
			exit "$$status"; \
		fi; \
		exit "$$cleanup_status"

research-smoke-init:
	@echo "objective_id=$(RESEARCH_SMOKE_OBJECTIVE_ID)"
	@$(MAKE) --no-print-directory auto-research-init \
		GOAL="$(RESEARCH_SMOKE_GOAL) ($(RESEARCH_SMOKE_OBJECTIVE_ID))" \
		OBJECTIVE_ID="$(RESEARCH_SMOKE_OBJECTIVE_ID)" \
		TARGET_HARNESS="$(RESEARCH_SMOKE_TARGET_HARNESS)" \
		TARGET_MODEL="$(RESEARCH_SMOKE_TARGET_MODEL)" \
		LOOP_EXECUTION_MODE="$(RESEARCH_SMOKE_LOOP_EXECUTION_MODE)" \
		MAX_REVISIONS="$(RESEARCH_SMOKE_MAX_REVISIONS)" \
		MAX_PARALLEL_LOOPS="$(RESEARCH_SMOKE_MAX_PARALLEL_LOOPS)" \
		BENCHMARK_REPEATS="$(RESEARCH_SMOKE_BENCHMARK_REPEATS)" \
		BENCHMARK_REPEAT_PARALLEL="$(RESEARCH_SMOKE_BENCHMARK_REPEAT_PARALLEL)" \
		RESEARCH_REPEATS="$(RESEARCH_SMOKE_RESEARCH_REPEATS)" \
		RESEARCH_REPEAT_PARALLEL="$(RESEARCH_SMOKE_RESEARCH_REPEAT_PARALLEL)" \
		CONTROL_PROVIDER="$(RESEARCH_SMOKE_CONTROL_PROVIDER)" \
		CONTROL_MODEL="$(RESEARCH_SMOKE_CONTROL_MODEL)" \
		PI_BINARY="$(PI_BINARY)"

research-smoke-approve:
	@$(MAKE) --no-print-directory auto-research-approve-scenario \
		OBJECTIVE_ID="$(RESEARCH_SMOKE_OBJECTIVE_ID)" \
		PI_BINARY="$(PI_BINARY)"

research-smoke-cleanup:
	@set -euo pipefail; \
		state_path="$(RESEARCH_SMOKE_STATE_PATH)"; \
		scenario_root=""; \
		scenario_preexisting="0"; \
		benchmark_dir=""; \
		if [ -f "$$state_path" ]; then \
			scenario_slug="$$(sed -n 's/^scenario_slug: //p' "$$state_path" | tail -n 1)"; \
			if [ -n "$$scenario_slug" ] && [ "$$scenario_slug" != "null" ]; then \
				scenario_root="$(CURDIR)/scenarios/$$scenario_slug"; \
				if [ -e "$$scenario_root" ]; then \
					scenario_preexisting="1"; \
				fi; \
			fi; \
			best_benchmark_ref="$$(sed -n 's/^best_benchmark_ref: //p' "$$state_path" | tail -n 1)"; \
			if [ -n "$$best_benchmark_ref" ] && [ "$$best_benchmark_ref" != "null" ]; then \
				benchmark_dir="$$(dirname "$$best_benchmark_ref")"; \
			fi; \
		fi; \
		if [ -n "$$benchmark_dir" ] && [ -d "$$benchmark_dir" ]; then \
			rm -rf "$$benchmark_dir"; \
		fi; \
		if [ "$$scenario_preexisting" = "0" ] && [ -n "$$scenario_root" ] && [ -d "$$scenario_root" ]; then \
			rm -rf "$$scenario_root"; \
		fi; \
		if [ -d "$(RESEARCH_SMOKE_OBJECTIVE_ROOT)" ]; then \
			rm -rf "$(RESEARCH_SMOKE_OBJECTIVE_ROOT)"; \
		fi

experiment-run:
	$(call require_var,SCENARIO)
	$(call require_var,HARNESS)
	$(call require_var,MODEL)
	@$(RAIDAR) experiment run \
		--scenario "$(SCENARIO)" \
		--harness "$(HARNESS)" \
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

auto-research-init:
	$(call require_var,GOAL)
	$(call require_var,TARGET_HARNESS)
	$(call require_var,TARGET_MODEL)
	@$(AUTO_RESEARCHER) init \
		--goal "$(GOAL)" \
		--target-harness "$(TARGET_HARNESS)" \
		--target-model "$(TARGET_MODEL)" \
		--loop-execution-mode "$(LOOP_EXECUTION_MODE)" \
		--max-revisions "$(MAX_REVISIONS)" \
		--max-parallel-loops "$(MAX_PARALLEL_LOOPS)" \
		--benchmark-repeats "$(BENCHMARK_REPEATS)" \
		--benchmark-repeat-parallel "$(BENCHMARK_REPEAT_PARALLEL)" \
		--research-repeats "$(RESEARCH_REPEATS)" \
		--research-repeat-parallel "$(RESEARCH_REPEAT_PARALLEL)" \
		--control-provider "$(CONTROL_PROVIDER)" \
		--control-model "$(CONTROL_MODEL)" \
		--pi-binary "$(PI_BINARY)" \
		$(if $(OBJECTIVE_ID),--objective-id "$(OBJECTIVE_ID)",)

auto-research-approve-scenario:
	$(call require_var,OBJECTIVE_ID)
	@$(AUTO_RESEARCHER) approve-scenario \
		--objective-id "$(OBJECTIVE_ID)" \
		--pi-binary "$(PI_BINARY)"

auto-research-run:
	$(call require_var,OBJECTIVE_ID)
	@$(AUTO_RESEARCHER) run \
		--objective-id "$(OBJECTIVE_ID)" \
		--pi-binary "$(PI_BINARY)"

auto-research-status:
	$(call require_var,OBJECTIVE_ID)
	@$(AUTO_RESEARCHER) status \
		--objective-id "$(OBJECTIVE_ID)" \
		--pi-binary "$(PI_BINARY)"

auto-research-report:
	$(call require_var,OBJECTIVE_ID)
	@$(AUTO_RESEARCHER) report \
		--objective-id "$(OBJECTIVE_ID)" \
		--pi-binary "$(PI_BINARY)"

quality:
	@$(MAKE) --no-print-directory smoke-dry-run-check
	@$(RAIDAR) quality gates
	@cd auto_researcher && uv run --project . --extra dev python -m ruff format --check .
	@cd auto_researcher && uv run --project . --extra dev python -m ruff check .
	@cd auto_researcher && uv run --project . --extra dev python -m mypy src tests
	@cd auto_researcher && uv run --project . --extra dev python -m pytest tests -x --tb=short
