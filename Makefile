SHELL := /bin/bash

RAIDAR := uv run --project orchestrator raidar

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
STARTER_ROOT ?=
PROMPT_ENTRY ?=
DIFFICULTY ?=
CATEGORY ?=
TIMEOUT_SEC ?=

SMOKE_SCENARIO := scenarios/hello-world-smoke/v001/scenario.yaml
SMOKE_HARNESS := codex-cli
SMOKE_MODEL := codex/gpt-5.4-low

ifeq ($(firstword $(MAKECMDGOALS)),matrix-run)
MATRIX_ARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
$(foreach goal,$(MATRIX_ARGS),$(eval $(goal):;@:))
endif

.PHONY: help \
	env-setup harness-list harness-validate scenario-list scenario-init scenario-info scenario-validate \
	smoke experiment-run matrix-run \
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
	@echo "  make harness-validate HARNESS=codex-cli MODEL=codex/gpt-5.4-low"
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
	@echo "  make smoke                                             Run the default smoke scenario on codex-cli with codex/gpt-5.4-low"
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

smoke:
	@$(RAIDAR) run \
		--scenario "$(SMOKE_SCENARIO)" \
		--harness "$(SMOKE_HARNESS)" \
		--model "$(SMOKE_MODEL)" \
		--repeats 1 \
		--repeat-parallel "$(RUN_PARALLELISM)" \
		--rerun-unscored "$(RERUN_UNSCORED)" \
		$(if $(TIMEOUT_SEC),--timeout "$(TIMEOUT_SEC)",)

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
		$(if $(TIMEOUT_SEC),--timeout "$(TIMEOUT_SEC)",)

experiments-list:
	@$(RAIDAR) experiments list \
		$(if $(EVALUATION_PROFILE),--evaluation-profile "$(EVALUATION_PROFILE)",) \
		$(if $(LIMIT),--limit "$(LIMIT)",)

experiments-prune:
	@$(RAIDAR) experiments prune --dry-run \
		$(if $(KEEP_PER_MODEL),--keep-per-model "$(KEEP_PER_MODEL)",)

quality:
	@$(RAIDAR) quality gates
