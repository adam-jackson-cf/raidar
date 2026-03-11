SHELL := /bin/bash

RAIDAR := uv run --project orchestrator raidar

SCENARIO ?=
SCENARIO_DIR ?=
SCENARIO_REVISION ?=
NAME ?=
HARNESS ?=
MODEL ?=
CONFIG ?= matrix.yaml
RUN_COUNT ?= 1
RUN_PARALLELISM ?= 1
RERUN_UNSCORED ?= 0
EVALUATION_PROFILE ?=
LIMIT ?=
KEEP_PER_MODEL ?=
STARTER_ROOT ?=
PROMPT_ENTRY ?=
DIFFICULTY ?=
CATEGORY ?=
TIMEOUT_SEC ?=

.PHONY: help \
	env-setup harness-list harness-validate scenario-list scenario-init scenario-info scenario-validate \
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
	@echo "  make harness-validate HARNESS=codex-cli MODEL=codex/gpt-5.4-high"
	@echo "                                                        Validate one AgentSpec candidate"
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
	@echo "  make experiment-run SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml HARNESS=... MODEL=..."
	@echo "                                                        Run one scenario yaml for one AgentSpec"
	@echo "  make matrix-run SCENARIO=... [CONFIG=matrix.yaml]"
	@echo "                                                        Run a scenario across a matrix config"
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
	$(call require_var,MODEL)
	@$(RAIDAR) harness validate --harness "$(HARNESS)" --model "$(MODEL)"

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
		--rerun-unscored "$(RERUN_UNSCORED)"

matrix-run:
	$(call require_var,SCENARIO)
	@$(RAIDAR) matrix --scenario "$(SCENARIO)" --config "$(CONFIG)"

experiments-list:
	@$(RAIDAR) experiments list \
		$(if $(EVALUATION_PROFILE),--evaluation-profile "$(EVALUATION_PROFILE)",) \
		$(if $(LIMIT),--limit "$(LIMIT)",)

experiments-prune:
	@$(RAIDAR) experiments prune --dry-run \
		$(if $(KEEP_PER_MODEL),--keep-per-model "$(KEEP_PER_MODEL)",)

quality:
	@$(RAIDAR) quality gates
