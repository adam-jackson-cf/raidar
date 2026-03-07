SHELL := /bin/bash

RAIDAR := uv run --project orchestrator raidar

TASK ?=
TASK_DIR ?=
TASK_VERSION ?=
NAME ?=
AGENT ?=
MODEL ?=
CONFIG ?= matrix.yaml
REPEATS ?= 1
REPEAT_PARALLEL ?= 1
RETRY_VOID ?= 0
METRIC_PROFILE ?=
LIMIT ?=
KEEP_PER_MODEL ?=
SCAFFOLD_ROOT ?=
PROMPT_ENTRY ?=
DIFFICULTY ?=
CATEGORY ?=
TIMEOUT_SEC ?=

.PHONY: help \
	env-setup provider-list provider-validate task-init task-info task-validate \
	suite-run matrix-run \
	evals-list evals-prune \
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
	@echo "  make env-setup"
	@echo "  make provider-list"
	@echo "  make provider-validate AGENT=codex-cli MODEL=codex/gpt-5.2-high"
	@echo "  make task-init TASK_DIR=tasks/new-task TASK_VERSION=v001"
	@echo "  make task-info TASK_DIR=tasks/homepage-implementation/v001"
	@echo "  make task-validate TASK=tasks/homepage-implementation/v001/task.yaml"
	@echo "  make quality"
	@echo ""
	@echo "Eval orchestration:"
	@echo "  make suite-run TASK=... AGENT=... MODEL=... [REPEATS=1 REPEAT_PARALLEL=1 RETRY_VOID=0]"
	@echo "  make matrix-run TASK=... [CONFIG=matrix.yaml]"
	@echo "  make evals-list [METRIC_PROFILE=...] [LIMIT=...]"
	@echo "  make evals-prune [KEEP_PER_MODEL=1]"

env-setup:
	@$(RAIDAR) env setup

provider-list:
	@$(RAIDAR) provider list

provider-validate:
	$(call require_var,AGENT)
	$(call require_var,MODEL)
	@$(RAIDAR) provider validate --agent "$(AGENT)" --model "$(MODEL)"

task-init:
	$(call require_var,TASK_DIR)
	@$(RAIDAR) task init \
		--path "$(TASK_DIR)" \
		$(if $(NAME),--name "$(NAME)",) \
		$(if $(TASK_VERSION),--task-version "$(TASK_VERSION)",) \
		$(if $(SCAFFOLD_ROOT),--scaffold-root "$(SCAFFOLD_ROOT)",) \
		$(if $(PROMPT_ENTRY),--prompt-entry "$(PROMPT_ENTRY)",) \
		$(if $(DIFFICULTY),--difficulty "$(DIFFICULTY)",) \
		$(if $(CATEGORY),--category "$(CATEGORY)",) \
		$(if $(TIMEOUT_SEC),--timeout "$(TIMEOUT_SEC)",)

task-info:
	$(call require_var,TASK_DIR)
	@$(RAIDAR) info --task "$(TASK_DIR)"

task-validate:
	$(call require_var,TASK)
	@$(RAIDAR) task validate --task "$(TASK)"

suite-run:
	$(call require_var,TASK)
	$(call require_var,AGENT)
	$(call require_var,MODEL)
	@$(RAIDAR) suite run \
		--task "$(TASK)" \
		--agent "$(AGENT)" \
		--model "$(MODEL)" \
		--repeats "$(REPEATS)" \
		--repeat-parallel "$(REPEAT_PARALLEL)" \
		--retry-void "$(RETRY_VOID)"

matrix-run:
	$(call require_var,TASK)
	@$(RAIDAR) matrix --task "$(TASK)" --config "$(CONFIG)"

evals-list:
	@$(RAIDAR) evals list \
		$(if $(METRIC_PROFILE),--metric-profile "$(METRIC_PROFILE)",) \
		$(if $(LIMIT),--limit "$(LIMIT)",)

evals-prune:
	@$(RAIDAR) evals prune --dry-run \
		$(if $(KEEP_PER_MODEL),--keep-per-model "$(KEEP_PER_MODEL)",)

quality:
	@$(RAIDAR) quality gates
