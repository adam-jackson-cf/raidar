SHELL := /bin/bash

RAIDAR := uv run --project orchestrator raidar

SCENARIO ?=
SCENARIO_DIR ?=
SCENARIO_REVISION ?=
NAME ?=
AGENT ?=
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
	env-setup agent-list agent-validate scenario-init scenario-info scenario-validate \
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
	@echo "  make agent-list                                        List supported agent ids"
	@echo "  make agent-validate AGENT=codex-cli MODEL=codex/gpt-5.4-high"
	@echo "                                                        Validate one agent/model pair"
	@echo "  make scenario-init SCENARIO_DIR=scenarios/new-scenario SCENARIO_REVISION=v001"
	@echo "                                                        Scaffold a new scenario"
	@echo "  make scenario-info SCENARIO_DIR=scenarios/homepage-implementation/v001"
	@echo "                                                        Inspect metrics, gates, rules, and visual config"
	@echo "  make scenario-validate SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml"
	@echo "                                                        Validate a scenario contract"
	@echo "  make quality                                           Run repo quality gates"
	@echo ""
	@echo "Experiment orchestration:"
	@echo "  make experiment-run SCENARIO=... AGENT=... MODEL=... [RUN_COUNT=1 RUN_PARALLELISM=1 RERUN_UNSCORED=0]"
	@echo "                                                        Run one scenario for one agent/model pair"
	@echo "  make matrix-run SCENARIO=... [CONFIG=matrix.yaml]"
	@echo "                                                        Run a scenario across a matrix config"
	@echo "  make experiments-list [EVALUATION_PROFILE=...] [LIMIT=...]"
	@echo "                                                        List stored experiments and summaries"
	@echo "  make experiments-prune [KEEP_PER_MODEL=1]"
	@echo "                                                        Preview artifact pruning decisions"

env-setup:
	@$(RAIDAR) env setup

agent-list:
	@$(RAIDAR) agent list

agent-validate:
	$(call require_var,AGENT)
	$(call require_var,MODEL)
	@$(RAIDAR) agent validate --agent "$(AGENT)" --model "$(MODEL)"

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
	@$(RAIDAR) info --scenario "$(SCENARIO_DIR)"

scenario-validate:
	$(call require_var,SCENARIO)
	@$(RAIDAR) scenario validate --scenario "$(SCENARIO)"

experiment-run:
	$(call require_var,SCENARIO)
	$(call require_var,AGENT)
	$(call require_var,MODEL)
	@$(RAIDAR) experiment run \
		--scenario "$(SCENARIO)" \
		--agent "$(AGENT)" \
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
