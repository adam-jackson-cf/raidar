SHELL := /bin/bash

# Generated data root (git-ignored): jobs, Assessment stores, analysis output, imported problem
# content, scratch. RAIDAR_VAR relocates it; Raidar reads the same variable.
RAIDAR_VAR ?= $(CURDIR)/var
override RAIDAR_VAR := $(abspath $(RAIDAR_VAR))
export RAIDAR_VAR
REPO_TMP_DIR ?= $(RAIDAR_VAR)/tmp
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

# Docker target (raidar.engine.target): TARGET=<name> selects an entry of ~/.config/raidar/targets.toml
# (else $RAIDAR_TARGET, then the file's `default`; none of these is an error).
TARGET ?=
TARGET_ENV := $(if $(TARGET),export RAIDAR_TARGET="$(TARGET)" &&,)

# Secrets: targets that call model providers run under `infisical run` (project bound by
# orchestrator/.infisical.json, env INFISICAL_ENV) when the infisical CLI is installed and
# logged in; otherwise they fall back to orchestrator/.env. SECRETS=infisical fails instead
# of falling back; SECRETS=dotenv skips Infisical and fails when orchestrator/.env is missing.
# Under auto with neither source, the target says so and goes on with the shell environment
# (file-backed Codex auth needs no secret). Detection runs at recipe time, presence
# only: no secret value is ever printed. Under Infisical the CLI does not load .env.
SECRETS ?= auto
INFISICAL_ENV ?= dev
INFISICAL_PROJECT_ID := $(shell sed -n 's/.*"workspaceId"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' orchestrator/.infisical.json 2>/dev/null)
INFISICAL_RUN := infisical run --silent --env=$(INFISICAL_ENV) --projectId=$(INFISICAL_PROJECT_ID) --
SECRETS_DETECT := secrets_cmd=""; \
	if [ "$(SECRETS)" != dotenv ] && [ -n "$(INFISICAL_PROJECT_ID)" ] && command -v infisical >/dev/null 2>&1 \
		&& $(INFISICAL_RUN) true </dev/null >/dev/null 2>&1; then \
		secrets_cmd="RAIDAR_SECRETS_SOURCE=infisical $(INFISICAL_RUN)"; \
		echo "secrets: Infisical (env $(INFISICAL_ENV))" >&2; \
	elif [ "$(SECRETS)" = infisical ]; then \
		echo "SECRETS=infisical but the infisical CLI is missing or not logged in; run 'infisical login' in a terminal" >&2; exit 1; \
	elif [ -f orchestrator/.env ]; then echo "secrets: orchestrator/.env (Infisical unavailable or SECRETS=dotenv)" >&2; \
	elif [ "$(SECRETS)" = dotenv ]; then \
		echo "SECRETS=dotenv but orchestrator/.env does not exist; create it from orchestrator/.env.example or use Infisical (SECRETS=auto)" >&2; exit 1; \
	else echo "secrets: none (Infisical unavailable and no orchestrator/.env); only the shell environment and file-backed Codex auth are available, and the credential preflight refuses an arm without a credential" >&2; fi;
RAIDAR_SECRETS := $(SECRETS_DETECT) $(REPO_RUNTIME_ENV) $$secrets_cmd uv run --project orchestrator raidar

# Schema v2 adopted Harbor's vocabulary (requirements change log 2026-10-02). A variable
# under its old name, on the command line or in the environment, fails loudly instead of
# being ignored.
RENAMED_VARS := RUN:JOB RUNS:JOBS RUN_ID:JOB_ID RUNS_DIR:JOBS_DIR HARNESS:AGENT \
	HARNESS_VERSION:AGENT_VERSION BLOCK:ATTEMPT SMOKE_BLOCKS:SMOKE_ATTEMPTS \
	ORACLE_BUNDLE:VERIFIER_BUNDLE REVIEW_RUNS_ROOT:REVIEW_JOBS_ROOT QUALITY_RUNS:QUALITY_JOBS \
	ALLOW_DEV_RUNS:ALLOW_DEV_JOBS SMOKE_RUNS_DIR:SMOKE_JOBS_DIR
$(foreach pair,$(RENAMED_VARS),$(if $(filter command line environment environment override,$(origin $(firstword $(subst :, ,$(pair))))),$(error $(firstword $(subst :, ,$(pair)))= was renamed $(lastword $(subst :, ,$(pair)))= (schema v2 uses Harbor's vocabulary; see README Concepts))))

# Shared public workflow defaults.
comma := ,
AGENT ?=
PROVIDER ?=
MODEL ?=
REASONING_EFFORT ?=
DEVICE_AUTH ?=
EXPERIMENT ?=
EXPERIMENT_SET ?=
LOCK_CHECK ?=
TIMEOUT_SEC ?=

# Experiment-path smoke workflow: the smoke experiment (kind smoke) and the agent benchmark
# (kind benchmark) are locked under var/tmp/smoke with the target's overrides and run through
# Harbor (Docker; the TARGET target).
SMOKE_EXPERIMENT ?= lab/experiments/smoke/python__circuit_eval-cp1__agent-health/experiment.yaml
SMOKE_AGENTS_EXPERIMENT ?= lab/experiments/benchmark/python__circuit_eval-cp1__agents/experiment.yaml
SMOKE_JOBS_DIR ?= $(RAIDAR_VAR)/jobs
SMOKE_LOCK_DIR ?= $(REPO_TMP_DIR)/smoke
SMOKE_ATTEMPTS ?= 1
SMOKE_CONCURRENCY ?= 1
# smoke-agents arms: one per agent the Python pack bakes in (the arm ids of the
# agent benchmark experiment). SMOKE_ARM selects one of them.
SMOKE_ARM ?=
SMOKE_ARMS := codex claude
ifneq ($(strip $(SMOKE_ARM)),)
ifeq ($(filter $(SMOKE_ARM),$(SMOKE_ARMS)),)
$(error Unknown SMOKE_ARM '$(SMOKE_ARM)'; expected one of: $(SMOKE_ARMS))
endif
endif
# agent-smoke: the smoke experiment with its one agent replaced. AGENT is a Harbor agent name
# (codex, claude-code). Versions must equal the Python pack pins
# (lab/environment/python/pack.yaml); the lock refuses others (RT5).
# Provider/model/effort default per agent: codex runs gpt-6-luna at low effort;
# claude-code runs claude-haiku-4-5, which has no effort levels, so its effort is unset.
# REASONING_EFFORT given (even empty: `REASONING_EFFORT=`) wins; empty means unset.
AGENT_SMOKE_AGENT ?= codex
AGENT_SMOKE_PROVIDER_codex := openai
AGENT_SMOKE_MODEL_codex := gpt-6-luna
AGENT_SMOKE_REASONING_codex := low
AGENT_SMOKE_PROVIDER_claude-code := anthropic
AGENT_SMOKE_MODEL_claude-code := claude-haiku-4-5
AGENT_SMOKE_REASONING_claude-code :=
AGENT_SMOKE_VERSION_codex := 0.156.1
AGENT_SMOKE_VERSION_claude-code := 2.1.285
AGENT_SMOKE_EFFECTIVE_AGENT = $(or $(AGENT),$(AGENT_SMOKE_AGENT))
AGENT_SMOKE_EFFECTIVE_PROVIDER = $(or $(PROVIDER),$(AGENT_SMOKE_PROVIDER_$(AGENT_SMOKE_EFFECTIVE_AGENT)))
AGENT_SMOKE_EFFECTIVE_MODEL = $(or $(MODEL),$(AGENT_SMOKE_MODEL_$(AGENT_SMOKE_EFFECTIVE_AGENT)))
AGENT_SMOKE_EFFECTIVE_REASONING = $(if $(filter command line environment,$(origin REASONING_EFFORT)),$(strip $(REASONING_EFFORT)),$(AGENT_SMOKE_REASONING_$(AGENT_SMOKE_EFFECTIVE_AGENT)))
AGENT_SMOKE_EFFECTIVE_VERSION = $(or $(AGENT_VERSION),$(AGENT_SMOKE_VERSION_$(AGENT_SMOKE_EFFECTIVE_AGENT)))

.PHONY: help \
	env-setup agent-list agent-validate codex-auth-setup harbor-cleanup docker-check \
	target-list target-status target-up target-down target-init target-prune \
	experiment-init experiment-validate experiment-lock experiment-locks-check \
	experiment-plan experiment-execute experiment-status \
	perf-check perf-fixture perf-gate health \
	ledger-check ledger-index \
	problem-list problem-info problem-import problem-validate problem-errata-draft \
	experiment-assess assess-regrade experiment-analyze jev-shadow-rubric \
	jev-export-labels jev-import-labels jev-export-blind jev-import-blind \
	jev-supersede-labels jev-record-split \
	jev-verify-promotion jev-promote \
	pack-info pack-build pack-audit pack-tool-provision \
	component-probe-trials guardrails-verify \
	smoke-dry-run-check orchestrator-smoke smoke-agents agent-smoke \
	review-fixture-synthetic \
	review-surface-data review-surface-build review-surface-serve review-surface-test \
	review-surface-quality-data quality-fixture-synthetic \
	structure-check release-archive \
	quality

define require_var
	@if [ -z "$($1)" ]; then \
		echo "Missing required variable: $1"; \
		exit 1; \
	fi
endef

help:
	@echo "Public workflow surface (experiment -> lock -> job -> assess -> analyze -> review)"
	@echo ""
	@echo "Environment:"
	@echo "  make env-setup                                         Bootstrap the orchestrator environment"
	@echo "  make agent-list                                        List supported agents (Harbor names) and model coverage"
	@echo "  make agent-validate AGENT=codex PROVIDER=openai MODEL=gpt-6-luna [REASONING_EFFORT=low]"
	@echo "                                                        Check one agent/model pairing and its host credentials"
	@echo "  make codex-auth-setup [DEVICE_AUTH=1]                 Create or validate file-backed Codex ChatGPT auth"
	@echo "  make harbor-cleanup [JOB=<job-dir>] [DRY_RUN=1]        Remove trial environments a stopped job left behind"
	@echo "  make quality                                           Run repo quality gates"
	@echo "  make structure-check                                   Check the repository layout (no versions in names, root and package allowlists, depth); part of make quality"
	@echo "  make release-archive REF=<name>@<N>                    Tag archive/<name>@<N> at HEAD before editing a released scorer or component in place (refuses an existing tag)"
	@echo "  Generated data lives under var/ (git-ignored; RAIDAR_VAR=<dir> relocates it): jobs, stores, analysis, problems, tmp"
	@echo "  Secrets: execute/smoke/assess/jev targets inject Infisical (project mac-os-cf, env dev) when the infisical"
	@echo "  CLI is logged in, else read orchestrator/.env. [SECRETS=auto|infisical|dotenv] [INFISICAL_ENV=dev]"
	@echo ""
	@echo "Docker target (TARGET=<name> from ~/.config/raidar/targets.toml, else \$$RAIDAR_TARGET, then its default; none of these is an error):"
	@echo "  make target-init                                       Write a commented ~/.config/raidar/targets.toml template (never overwrites)"
	@echo "  make target-list                                       List defined targets and the selected one"
	@echo "  make target-status [TARGET=name]                       Print the target's Docker facts; non-zero when unreachable or its data check fails"
	@echo "  make target-up|target-down [TARGET=name]               Start/stop a target's SSH-forwarded Docker socket (context raidar-<name>)"
	@echo "  make target-prune [TARGET=name] [DRY_RUN=1]            Remove stale Raidar trial Compose projects (nothing running) on the target"
	@echo "  make docker-check [TARGET=name]                        Make the target reachable (restart its transport) and run its data check"
	@echo ""
	@echo "Problems and runtime packs (Docker target as above):"
	@echo "  make problem-list                                      List registered problems with validation and import state"
	@echo "  make problem-info PROBLEM=scb/forge@38d627ecf668       Show one problem's steps, source pin, pack binding and validation"
	@echo "  make problem-import PROBLEM=scb/forge@38d627ecf668 [SOURCE_CHECKOUT=path] [UPDATE=1]"
	@echo "                                                        Import a pinned problem binding (task + host-only verifier bundle) into var/problems/ (pins and manifests: lab/problems/)"
	@echo "  make problem-validate PROBLEM=scb/forge@38d627ecf668 [ENGINE=direct|harbor] [NATIVE_RUNNER=path] [REPEATS=3] [CONCURRENCY=4] [AUDIT=0] [AUDIT_ONLY=1]"
	@echo "                                                        Verifier-validate an imported problem and write its report, with the"
	@echo "                                                        spec-determinacy audit (AUDIT_ONLY=1: audit only, into the committed report)"
	@echo "  make problem-errata-draft PROBLEM=scb/forge@38d627ecf668 OUT=<path> REGISTERED=<yyyy-mm-dd>"
	@echo "                                                        Draft a problem errata from its report's audit (review, then commit it)"
	@echo "  make pack-info [RUNTIME=python]                       Show the pack image tag, lock hashes and tool provisioning state"
	@echo "  make pack-build [RUNTIME=python] [FORCE=1]            Build the agent image once per lock hash and assert no scorer tools"
	@echo "  make pack-audit [RUNTIME=python]                      Assert the built agent image has pinned agents, no scorer tools or browsers"
	@echo "  make pack-tool-provision TOOL=<tool> [RUNTIME=python] Provision one host-parse measurement tool into the lock-keyed cache"
	@echo "  make component-probe-trials [PROBE_EVIDENCE_DIR=<dir>] Real codex/gpt-6-luna low trials proving every built-in component's discovery probe"
	@echo "  make guardrails-verify                                python-guardrails rule tests (86 rules, the test-class and --autofix canaries included): every rule (derived from the pack's config and codes) fires on its violating canary and"
	@echo "                                                        passes its clean one against the pack installed in the pack image, plus a mutant-pack negative"
	@echo "                                                        control (Docker target, PyPI; not in make quality)"
	@echo ""
	@echo "Experiments:"
	@echo "  make experiment-init EXPERIMENT=lab/experiments/ab-test/<id>/experiment.yaml [PROBLEM=<ref>] [EXPERIMENT_ID=]"
	@echo "                                                        Start a new ab-test: two-arm starter (baseline vs rules-file) plus stub rules file"
	@echo "  make experiment-validate EXPERIMENT=lab/experiments/<kind>/<id>/experiment.yaml [EXPERIMENT_SET='analysis.attempts=3']"
	@echo "                                                        Validate an experiment.yaml (schema, kind and naming rules, factor guard)"
	@echo "  make experiment-lock EXPERIMENT=... [LOCK_CHECK=1]     Write (or check, naming stale sections) <experiment>.lock.json; refuses runtime"
	@echo "                                                        incompatibilities (RT0-RT6); lock_sha256 covers execution inputs only"
	@echo "  make experiment-locks-check                           Fail unless every lab/experiments/*/*/*.lock.json is current (host Python patch aside); part of make quality"
	@echo "  make ledger-check                                     Check lab/ledger/ (fault and remedy records, links, status transitions, README index); part of make quality"
	@echo "  make ledger-index                                     Regenerate lab/ledger/README.md from the records"
	@echo "  make experiment-plan EXPERIMENT=... [LOCK=] [ARM=a] [ATTEMPT=0] [MAX_CHECKPOINTS=N] [CONCURRENCY=N] [PLAN_JSON=1]"
	@echo "                                                        Dry run: list every trial of a locked experiment with its hashes"
	@echo "  make experiment-execute EXPERIMENT=... [LOCK=] [ARM=a] [ATTEMPT=0] [MAX_CHECKPOINTS=N] [AGENT_OVERRIDE=oracle|nop] [CONCURRENCY=N] [JOBS_DIR=var/jobs] [JOB_ID=] [DETACH=1] [ALLOW_UNSMOKED=1]"
	@echo "                                                        Run a locked experiment through Harbor as one job; writes step results, validity and N1 phases"
	@echo "                                                        DETACH=1: run it under a supervisor in its own session (survives the launching shell"
	@echo "                                                        or agent session; pid, log and exit record in <job dir>/detach/); required for long jobs"
	@echo "                                                        Non-smoke kinds are refused without a clean matching smoke job (same code, pack, agents, target; the agent changed /app in every step);"
	@echo "                                                        ALLOW_UNSMOKED=1 overrides and records it in job.json (admission)"
	@echo "  make experiment-status JOB=<job-dir> [LOG_LINES=20]   A job's state (running, finished, failed, died), trials recorded of selected, log tail"
	@echo "  make health JOB=<job-dir>                             Recompute every trial's health.json (V/H/L/P/T checks) and append fault-candidates.jsonl; exit 1 on a violation"
	@echo "  make experiment-assess JOB=<job-dir> STORE=<assessment-store> SCORERS=\"correctness@1 ...\" [LOCK=] [ASSESSMENT_CONFIG=] [REPLAY=1] [CONCURRENCY=N] [ASSESS_OUT=report.json]"
	@echo "                                                        Score a job's stored trials into an Assessment store (never re-runs agents; AS1-gated)"
	@echo "                                                        (process-compliance@6 is deterministic only;"
	@echo "                                                        with gate-effort@10 it first grades guardrail-gate@4 pre-fix snapshots into STORE, and with"
	@echo "                                                        code-quality@3 it first makes deferred coverage runs (verifier_coverage: offline) into STORE: Docker target;"
	@echo "                                                        CONCURRENCY=N works on N trials at once, each trial's steps in order; default 1)"
	@echo "  make assess-regrade JOB=<trial-or-job-dir> [VERIFIER_BUNDLE=<dir>] [STORE=<assessment-store>] [COVERAGE_CHECK=1 [CONCURRENCY=N]] [REGRADE_OUT=report.json]"
	@echo "                                                        Rerun the verifier over stored step snapshots (and, with STORE, the stored pre-fix grades); fails if results differ;"
	@echo "                                                        COVERAGE_CHECK=1 instead makes each step's coverage run offline and compares it with the trial's own (or STORE's)"
	@echo "  make experiment-analyze JOBS=\"<job-dir> ...\" STORE=<assessment-store> ANALYSIS_OUT=<dir> [LOCK=] [ASSESSMENT_CONFIG=] [BUNDLE_SEARCH=<dir>...] [MIN_PAIRS=] [MAX_EXCLUSION_FRACTION=] [ALPHA=] [BOOTSTRAP_SAMPLES=10000] [MIN_EFFECTS=\"metric=delta ...\"] [ALLOW_DEV_JOBS=1] [POOL_OVERRIDE=<field>[,<field>]]"
	@echo "                                                        Paired deltas, CIs, curves, verdicts and sample size vs the baseline arm (thresholds from the lock;"
	@echo "                                                        overrides are labelled); benchmarks add a ranking view, smoke reports are stamped not citable;"
	@echo "                                                        2+ jobs pool only when provenance matches (POOL_OVERRIDE lifts named refusals, recorded; a records-manifest violation never lifts)"
	@echo "  make perf-check JOB=<job-dir> [MIN_SAMPLES=10] [PERF_STRICT=1]"
	@echo "                                                        Report each N1 phase p95 vs its budget plus trial concurrency/contention; breaches are warnings (exit 0) unless PERF_STRICT=1"
	@echo "  make perf-fixture [PERF_FIXTURE_DIR=<dir>] [PERF_FIXTURE_CONCURRENCY=1]"
	@echo "                                                        No-LLM fixture experiment (5 attempts x 2 arms) then strict perf check (Docker; builds the pack image if absent; fails, never skips, without Docker)"
	@echo "  make perf-gate [PERF_GATE_DIR=var/tmp/perf-gate]       Enforced N1 overhead gate: perf-fixture at concurrency 1 and 3, strict; fails on any breach (Docker; release/gate check, not in make quality)"
	@echo "  Jev targets below are DORMANT (Jev parked 2026-10-04: both policies off, out of scoring and analysis;"
	@echo "  kept for history and a future decision; see docs/reboot/requirements.md change log, F7):"
	@echo "  make jev-shadow-rubric SNAPSHOTS=<checkpoint-dirs> STORE=<assessment-store> [SPECS=<steps-dir>] [WITHIN=artifacts/app] [TRIAL=<id>] [REPLAY_ONLY=1] [OUT=report.json]"
	@echo "                                                        [dormant] Run the SCB rubric Jev question set in shadow; report coverage and input sufficiency"
	@echo "  make jev-export-labels POLICY=<policy> OUT=<labels.jsonl> [STORE=<assessment-store>] [LABELS=review-surface/data/jev-labels.json]"
	@echo "                                                        [dormant] Export review-surface Jev labels as calibration rows (one per decision)"
	@echo "  make jev-import-labels POLICY=<policy> STORE=<assessment-store> LABELS_FILE=<labels.jsonl> [REVIEWER=human|model REVIEWER_MODEL=<model id>]"
	@echo "                                                        [dormant] Import calibration labels into the store's Jev audit and show enforce readiness"
	@echo "  make jev-export-blind POLICY=<policy> STORE=<assessment-store> OUT=<blind.jsonl> [JEV_TRIALS=\"<trial> ...\"] [LIMIT=N] [REQUEST_IDS=\"<id> ...\"]"
	@echo "                                                        [dormant] Export answered, unlabelled decisions (state + questions) WITHOUT Jev's answers for a blind labeller"
	@echo "  make jev-import-blind POLICY=<policy> STORE=<assessment-store> ANSWERS_FILE=<answers.jsonl> [REVIEWER=human|model REVIEWER_MODEL=<model id>]"
	@echo "                                                        [dormant] Derive correct/false_positive/false_negative/uncertain from the labeller's own answers vs Jev's at 0.5, and import"
	@echo "  make jev-supersede-labels POLICY=<policy> STORE=<assessment-store> REASON=\"...\" (REQUEST_IDS=\"<id> ...\" | ALL_CURRENT=1)"
	@echo "                                                        [dormant] Take labels out of calibration without deleting them (append-only supersession)"
	@echo "  make jev-record-split POLICY=<policy> STORE=<assessment-store> SPLIT_ID=<id> CALIBRATION=\"<trial> ...\" EVALUATION=\"<trial> ...\" REASON=\"...\""
	@echo "                                                        [dormant] Record a calibration/evaluation trial split before labelling; jev-promote defaults to the latest"
	@echo "  make jev-verify-promotion POLICY=<policy> STORE=<assessment-store>"
	@echo "                                                        [dormant] Re-derive a policy's recorded promotion (gate, threshold, metrics, corpus sha256, reviewers, scorer version) from the audit"
	@echo "  make jev-promote POLICY=<policy> STORE=<assessment-store> [CALIBRATION=\"<trial> ...\" EVALUATION=\"<trial> ...\"] [DRY_RUN=1] [ALLOW_SCORER_BUMP=1]"
	@echo "                                                        [dormant] Fit and freeze the threshold on the calibration split; write mode: enforce + promotion only if it verifies"
	@echo "                                                        (refused while the scorer's minor bump is pending unless ALLOW_SCORER_BUMP=1)"
	@echo ""
	@echo "Smoke (real agent trials of lab/experiments/smoke and the agent benchmark; locks under var/tmp/smoke, jobs under var/jobs/):"
	@echo "  make orchestrator-smoke [SMOKE_ATTEMPTS=1] [SMOKE_CONCURRENCY=1] [TIMEOUT_SEC=]"
	@echo "                                                        One one-shot trial with codex + gpt-6-luna (low), correctness only"
	@echo "  make agent-smoke [AGENT=codex PROVIDER=openai MODEL=gpt-6-luna REASONING_EFFORT=low] [AGENT_VERSION=] [TIMEOUT_SEC=]"
	@echo "                                                        The same smoke with its agent replaced (versions default to the pack pins;"
	@echo "                                                        AGENT=claude-code defaults to claude-haiku-4-5, no effort; REASONING_EFFORT= unsets)"
	@echo "  make smoke-agents [SMOKE_ARM=codex|claude]             One trial per pack agent (codex: gpt-6-luna low; claude: claude-haiku-4-5)"
	@echo "  make smoke-dry-run-check                               Print the smoke command shapes (used by make quality)"
	@echo ""
	@echo "Review surface:"
	@echo "  make review-fixture-synthetic [REVIEW_FIXTURE_DIR=var/tmp/review-fixture]"
	@echo "                                                        Write the labelled synthetic review fixture (job roots + Assessment store)"
	@echo "  make review-surface-data REVIEW_JOBS_ROOT=<jobs-root>... [REVIEW_STORE=<store>...] [REVIEW_LOCK=<lock>...]"
	@echo "                                                        Project experiment jobs into review-surface data"
	@echo "  make review-surface-build                              Install and build the review-surface app"
	@echo "  make review-surface-serve [REVIEW_SURFACE_PORT=5950]   Serve the review surface app and API locally"
	@echo "  make review-surface-test [REVIEW_JOBS_ROOT=... REVIEW_STORE=... REVIEW_LOCK=...]"
	@echo "                                                        Run the review-surface end-to-end suite (Playwright) on the experiment synthetic"
	@echo "                                                        fixture plus any given jobs (one REVIEW_STORE for all roots, or one per root)"
	@echo "  make quality-fixture-synthetic [QUALITY_FIXTURE_DIR=var/tmp/quality-fixture]"
	@echo "                                                        Write the labelled synthetic Quality fixture (jobs + Assessment store)"
	@echo "  make review-surface-quality-data [QUALITY_JOBS=\"<jobs-root>...\" QUALITY_STORE=\"<store>...\"] [QUALITY_LOCK=<lock>...]"
	@echo "                                                        Project Quality element data, each jobs root against the store at the"
	@echo "                                                        same position (default: labelled synthetic fixture)"

RUNTIME ?= python
TOOL ?=
FORCE ?=

pack-info:
	@$(TARGET_ENV) $(RAIDAR) pack info "$(RUNTIME)"

pack-build:
	@$(TARGET_ENV) $(RAIDAR) pack build "$(RUNTIME)" $(if $(FORCE),--force,)

PROBE_EVIDENCE_DIR ?=

component-probe-trials:
	@$(TARGET_ENV) export RAIDAR_REAL_LLM_PROBES=1 && \
		$(if $(PROBE_EVIDENCE_DIR),export RAIDAR_PROBE_EVIDENCE_DIR="$(abspath $(PROBE_EVIDENCE_DIR))" &&,) \
		$(SECRETS_DETECT) cd orchestrator && $(REPO_RUNTIME_ENV) $$secrets_cmd uv run --project . --extra dev \
		python -m pytest tests/test_component_real_trial_probes.py -v

pack-audit:
	@$(TARGET_ENV) $(RAIDAR) pack audit "$(RUNTIME)"

# python-guardrails' own rule tests in the pack image: every implemented rule's violating canary
# fires and its clean canary passes (run_canaries.py; tests/test_component_python_guardrails_trial.py).
guardrails-verify: docker-check pack-build
	@$(TARGET_ENV) export RAIDAR_GUARDRAILS_VERIFY=1 && cd orchestrator && $(REPO_RUNTIME_ENV) \
		uv run --project . --extra dev python -m pytest tests/test_component_python_guardrails_trial.py \
		-k proven -v -s

pack-tool-provision:
	$(call require_var,TOOL)
	@$(RAIDAR) pack provision "$(RUNTIME)" "$(TOOL)"

# The synthetic review fixture: job roots <dir>/jobs and <dir>/review-only, store <dir>/store.
REVIEW_FIXTURE_DIR ?= $(REPO_TMP_DIR)/review-fixture
REVIEW_JOBS_ROOT ?=
REVIEW_STORE ?=
REVIEW_LOCK ?=

# $(1) job roots, $(2) Assessment stores.
define review_projection
	@$(REPO_RUNTIME_ENV) uv run --project orchestrator python -m raidar.evaluation.review.projection \
		$(foreach root,$(1),--jobs-root "$(abspath $(root))") \
		$(foreach store,$(2),--store "$(abspath $(store))") \
		$(foreach lock,$(REVIEW_LOCK),--lock "$(abspath $(lock))") \
		--output "$(REPO_TMP_DIR)/review-projection.json"
	@RAIDAR_REVIEW_PROJECTION="$(REPO_TMP_DIR)/review-projection.json" \
		node review-surface/scripts/build-review-data.mjs
endef

review-fixture-synthetic:
	@cd orchestrator && uv run --project . python -m raidar.evaluation.review.projection.fixture "$(abspath $(REVIEW_FIXTURE_DIR))"

review-surface-data:
	$(call require_var,REVIEW_JOBS_ROOT)
	$(call review_projection,$(REVIEW_JOBS_ROOT),$(REVIEW_STORE))

review-surface-build:
	@cd review-surface && npm install --no-fund --no-audit && npm run build

review-surface-serve: review-surface-data
	@if [ ! -f review-surface/dist/index.html ]; then $(MAKE) --no-print-directory review-surface-build; fi
	@cd review-surface && node server.mjs

QUALITY_FIXTURE_DIR ?= $(REPO_TMP_DIR)/quality-fixture
QUALITY_JOBS ?=
QUALITY_STORE ?=
QUALITY_LOCK ?=

# $(1) job roots and $(2) Assessment stores, paired by position; $(3) --synthetic or empty.
define quality_projection
	@$(REPO_RUNTIME_ENV) uv run --project orchestrator python -m raidar.evaluation.review.quality \
		$(foreach root,$(1),--jobs-root "$(abspath $(root))") \
		$(foreach store,$(2),--store "$(abspath $(store))") \
		$(foreach lock,$(QUALITY_LOCK) $(REVIEW_LOCK),--lock "$(abspath $(lock))") \
		--output "$(REPO_TMP_DIR)/quality-projection.json" $(3)
	@RAIDAR_QUALITY_PROJECTION="$(REPO_TMP_DIR)/quality-projection.json" \
		node review-surface/scripts/build-quality-data.mjs
endef

quality-fixture-synthetic:
	@$(REPO_RUNTIME_ENV) uv run --project orchestrator python -m raidar.evaluation.review.quality.synthetic "$(QUALITY_FIXTURE_DIR)"

review-surface-quality-data:
	$(if $(QUALITY_JOBS),$(if $(filter $(words $(QUALITY_JOBS)),$(words $(QUALITY_STORE))),,$(error QUALITY_JOBS and QUALITY_STORE need one store per jobs root, in the same order)))
ifeq ($(strip $(QUALITY_JOBS)),)
	@$(MAKE) --no-print-directory quality-fixture-synthetic
	$(call quality_projection,$(QUALITY_FIXTURE_DIR)/jobs,$(QUALITY_FIXTURE_DIR)/store,--synthetic)
else
	$(call quality_projection,$(QUALITY_JOBS),$(QUALITY_STORE),)
endif

# The suite runs on the synthetic review fixture under REVIEW_FIXTURE_DIR plus any given jobs.
# Given jobs and their stores: one REVIEW_STORE serves every REVIEW_JOBS_ROOT, otherwise there
# must be one store per root in the same order (anything else fails loudly, never silently drops
# jobs from the Quality data).
REVIEW_TEST_STORES = $(if $(filter 1,$(words $(REVIEW_STORE))),$(foreach root,$(REVIEW_JOBS_ROOT),$(REVIEW_STORE)),$(REVIEW_STORE))
# Quality data for the suite: the labelled Quality fixture, the review fixture's jobs (so trial
# detail shows its Quality panel) and any given jobs, each against its own store. The review
# fixture's review-only root is left out on purpose: it shows trials without Quality data.
REVIEW_TEST_QUALITY_JOBS = $(QUALITY_FIXTURE_DIR)/jobs $(REVIEW_FIXTURE_DIR)/jobs $(REVIEW_JOBS_ROOT)
REVIEW_TEST_QUALITY_STORES = $(QUALITY_FIXTURE_DIR)/store $(REVIEW_FIXTURE_DIR)/store $(REVIEW_TEST_STORES)

review-surface-test: quality-fixture-synthetic
	$(if $(filter $(words $(REVIEW_JOBS_ROOT)),$(words $(REVIEW_TEST_STORES))),,$(error REVIEW_JOBS_ROOT and REVIEW_STORE need one store for all roots or one store per root, in the same order))
	@$(MAKE) --no-print-directory review-fixture-synthetic
	$(call quality_projection,$(REVIEW_TEST_QUALITY_JOBS),$(REVIEW_TEST_QUALITY_STORES),--synthetic)
	$(call review_projection,$(REVIEW_FIXTURE_DIR)/jobs $(REVIEW_FIXTURE_DIR)/review-only $(REVIEW_JOBS_ROOT),$(REVIEW_FIXTURE_DIR)/store $(REVIEW_TEST_STORES))
	@cd review-surface && npm install --no-fund --no-audit && npm run build \
		&& npx playwright install chromium && npm test

env-setup:
	@$(RAIDAR_DEV) env setup --sync-arg --extra --sync-arg dev

agent-list:
	@$(RAIDAR) agent list

agent-validate:
	$(call require_var,AGENT)
	$(call require_var,PROVIDER)
	$(call require_var,MODEL)
	@$(RAIDAR_SECRETS) agent validate \
		--agent "$(AGENT)" \
		--provider "$(PROVIDER)" \
		--model "$(MODEL)" \
		$(if $(REASONING_EFFORT),--reasoning-effort "$(REASONING_EFFORT)",) \
		$(if $(TIMEOUT_SEC),--timeout "$(TIMEOUT_SEC)",)

codex-auth-setup:
	@$(RAIDAR) agent setup-auth \
		--agent "codex" \
		$(if $(filter 1 true yes on,$(DEVICE_AUTH)),--device-auth,)

harbor-cleanup:
	@$(TARGET_ENV) $(RAIDAR) harbor cleanup $(if $(JOB),--job "$(JOB)",) $(if $(DRY_RUN),--dry-run,)

docker-check:
	@$(TARGET_ENV) $(RAIDAR) target ensure

target-list:
	@$(RAIDAR) target list

target-status:
	@$(TARGET_ENV) $(RAIDAR) target status

target-up:
	@$(TARGET_ENV) $(RAIDAR) target up

target-down:
	@$(TARGET_ENV) $(RAIDAR) target down

target-init:
	@$(RAIDAR) target init

target-prune:
	@$(TARGET_ENV) $(RAIDAR) target prune $(if $(DRY_RUN),--dry-run,)

experiment-init:
	$(call require_var,EXPERIMENT)
	@$(RAIDAR) init "$(EXPERIMENT)" $(if $(PROBLEM),--problem "$(PROBLEM)",) $(if $(EXPERIMENT_ID),--id "$(EXPERIMENT_ID)",)

experiment-validate:
	$(call require_var,EXPERIMENT)
	@$(RAIDAR) validate "$(EXPERIMENT)" $(foreach item,$(EXPERIMENT_SET),--set "$(item)")

experiment-lock:
	$(call require_var,EXPERIMENT)
	@$(RAIDAR) lock "$(EXPERIMENT)" $(foreach item,$(EXPERIMENT_SET),--set "$(item)") $(if $(LOCK_CHECK),--check,)

# Every committed experiment lock must match a fresh lock of its experiment, section by section.
EXPERIMENT_LOCKS := $(sort $(wildcard lab/experiments/*/*/*.lock.json))

experiment-locks-check:
	@$(if $(EXPERIMENT_LOCKS),,$(error no experiment locks found under lab/experiments/))
	@$(foreach lock,$(EXPERIMENT_LOCKS),$(RAIDAR) lock "$(patsubst %.lock.json,%.yaml,$(lock))" --check --ignore-host-python &&) true

ledger-check:
	@$(RAIDAR) ledger check

ledger-index:
	@$(RAIDAR) ledger index

EXECUTION_SELECT = $(if $(LOCK),--lock "$(LOCK)",) $(foreach arm,$(ARM),--arm "$(arm)") \
	$(foreach attempt,$(ATTEMPT),--attempt "$(attempt)") \
	$(if $(MAX_CHECKPOINTS),--max-checkpoints "$(MAX_CHECKPOINTS)",) \
	$(if $(AGENT_OVERRIDE),--agent-override "$(AGENT_OVERRIDE)",) \
	$(if $(CONCURRENCY),--concurrency "$(CONCURRENCY)",) \
	$(foreach item,$(EXPERIMENT_SET),--set "$(item)")

experiment-plan:
	$(call require_var,EXPERIMENT)
	@$(RAIDAR) plan "$(EXPERIMENT)" $(EXECUTION_SELECT) $(if $(PLAN_JSON),--json,)

# DETACH=1: the job runs under a supervisor in its own session (raidar.engine.job.detach), so it
# survives the launching shell or agent session; its launch record, pid and log are in
# <job dir>/detach/. The supervisor reruns this target with DETACH=0 and the job id (the
# caller's other variables travel in MAKEFLAGS). Poll with make experiment-status JOB=<job dir>.
DETACH ?=
LOG_LINES ?= 20
EXECUTE_JOBS_DIR = $(if $(JOBS_DIR),$(abspath $(JOBS_DIR)),$(RAIDAR_VAR)/jobs)
DETACH_TOOL := $(REPO_RUNTIME_ENV) uv run --project orchestrator python -m raidar.engine.job.detach

experiment-execute: pack-build
	$(call require_var,EXPERIMENT)
ifeq ($(DETACH),1)
	@$(DETACH_TOOL) launch --experiment "$(EXPERIMENT)" --jobs-dir "$(EXECUTE_JOBS_DIR)" \
		$(if $(JOB_ID),--job-id "$(JOB_ID)",) -- $(MAKE) --no-print-directory -C "$(CURDIR)" \
		experiment-execute DETACH=0
else
	@$(TARGET_ENV) $(RAIDAR_SECRETS) run "$(EXPERIMENT)" $(EXECUTION_SELECT) \
		--jobs-dir "$(EXECUTE_JOBS_DIR)" $(if $(JOB_ID),--job-id "$(JOB_ID)",) $(if $(filter 1,$(ALLOW_UNSMOKED)),--allow-unsmoked,)
endif

experiment-status:
	$(call require_var,JOB)
	@$(DETACH_TOOL) status "$(JOB)" --lines "$(LOG_LINES)"

health:
	$(call require_var,JOB)
	@$(RAIDAR) health "$(JOB)"

perf-check:
	$(call require_var,JOB)
	@$(RAIDAR) perf check $(JOB) $(if $(MIN_SAMPLES),--min-samples "$(MIN_SAMPLES)",) $(if $(filter 1,$(PERF_STRICT)),--strict,)

PERF_FIXTURE_DIR ?=
PERF_FIXTURE_CONCURRENCY ?=

perf-fixture:
	@$(TARGET_ENV) $(RAIDAR) pack build python
	@$(TARGET_ENV) export RAIDAR_EXECUTION_DOCKER=1 && \
		$(if $(PERF_FIXTURE_DIR),export RAIDAR_PERF_FIXTURE_DIR="$(abspath $(PERF_FIXTURE_DIR))" &&,) \
		$(if $(PERF_FIXTURE_CONCURRENCY),export RAIDAR_PERF_FIXTURE_CONCURRENCY="$(PERF_FIXTURE_CONCURRENCY)" &&,) \
		cd orchestrator && $(REPO_RUNTIME_ENV) uv run --project . --extra dev \
		python -m pytest tests/test_overhead_fixture_run.py -v -s -rs

# The enforced N1 overhead gate: the no-LLM fixture at fixed concurrency 1 and 3, each
# checked strictly against the accepted budgets. Needs Docker, so it is a release/gate
# check, not part of make quality (whose Docker tests stay opt-in and skipped).
PERF_GATE_DIR ?= $(REPO_TMP_DIR)/perf-gate

perf-gate: docker-check
	@rm -rf "$(PERF_GATE_DIR)"
	@$(MAKE) --no-print-directory perf-fixture PERF_FIXTURE_CONCURRENCY=1 PERF_FIXTURE_DIR="$(PERF_GATE_DIR)/concurrency-1"
	@$(MAKE) --no-print-directory perf-fixture PERF_FIXTURE_CONCURRENCY=3 PERF_FIXTURE_DIR="$(PERF_GATE_DIR)/concurrency-3"

problem-list:
	@$(RAIDAR) problem list $(if $(PROBLEM_STORE),--store "$(PROBLEM_STORE)",)

problem-info:
	$(call require_var,PROBLEM)
	@$(RAIDAR) problem info "$(PROBLEM)" $(if $(PROBLEM_STORE),--store "$(PROBLEM_STORE)",)

problem-import:
	$(call require_var,PROBLEM)
	@$(RAIDAR) problem import $(PROBLEM) $(if $(SOURCE_CHECKOUT),--source-checkout "$(SOURCE_CHECKOUT)",) $(if $(UPDATE),--update,)

problem-validate:
	$(call require_var,PROBLEM)
	@$(TARGET_ENV) $(RAIDAR) problem validate "$(PROBLEM)" \
		$(if $(ENGINE),--engine "$(ENGINE)",) $(if $(REPEATS),--repeats "$(REPEATS)",) \
		$(if $(NATIVE_RUNNER),--native-runner "$(NATIVE_RUNNER)",) \
		$(if $(SOURCE_CHECKOUT),--source-checkout "$(SOURCE_CHECKOUT)",) \
		$(if $(filter 0,$(AUDIT)),--no-audit,) $(if $(AUDIT_ONLY),--audit-only,) \
		$(if $(CONCURRENCY),--concurrency "$(CONCURRENCY)",) \
		$(if $(PROBLEM_STORE),--store "$(PROBLEM_STORE)",)

problem-errata-draft:
	$(call require_var,PROBLEM)
	$(call require_var,OUT)
	$(call require_var,REGISTERED)
	@$(RAIDAR) problem errata-draft "$(PROBLEM)" --output "$(OUT)" --registered "$(REGISTERED)" \
		$(if $(PROBLEM_STORE),--store "$(PROBLEM_STORE)",)

JOB ?=
VERIFIER_BUNDLE ?=
REGRADE_OUT ?=

experiment-assess:
	$(call require_var,JOB)
	$(call require_var,STORE)
	$(call require_var,SCORERS)
	@$(TARGET_ENV) $(RAIDAR_SECRETS) assess --rescore "$(if $(wildcard $(JOB)/trials),$(JOB)/trials,$(JOB))" --store "$(STORE)" \
		$(foreach scorer,$(SCORERS),--scorer "$(scorer)") \
		$(if $(ASSESSMENT_CONFIG),--config "$(ASSESSMENT_CONFIG)",) $(if $(LOCK),--lock "$(LOCK)",) $(if $(REPLAY),--replay,) \
		$(if $(CONCURRENCY),--concurrency "$(CONCURRENCY)",) \
		$(if $(ASSESS_OUT),--out "$(ASSESS_OUT)",)

assess-regrade:
	$(call require_var,JOB)
	@$(TARGET_ENV) $(RAIDAR) assess --regrade "$(JOB)" $(if $(LOCK),--lock "$(LOCK)",) $(if $(VERIFIER_BUNDLE),--bundle "$(VERIFIER_BUNDLE)",) $(if $(STORE),--store "$(STORE)",) $(if $(COVERAGE_CHECK),--coverage-check,) $(if $(CONCURRENCY),--concurrency "$(CONCURRENCY)",) $(if $(REGRADE_OUT),--out "$(REGRADE_OUT)",)

experiment-analyze:
	$(call require_var,JOBS)
	$(call require_var,STORE)
	$(call require_var,ANALYSIS_OUT)
	@$(RAIDAR) analyze $(foreach job,$(JOBS),"$(job)") --store "$(STORE)" --output "$(ANALYSIS_OUT)" $(if $(LOCK),--lock "$(LOCK)",) $(if $(ASSESSMENT_CONFIG),--assessment-config "$(ASSESSMENT_CONFIG)",) $(foreach dir,$(BUNDLE_SEARCH),--bundle-search "$(dir)") $(if $(MIN_PAIRS),--min-pairs "$(MIN_PAIRS)",) $(if $(MAX_EXCLUSION_FRACTION),--max-exclusion-fraction "$(MAX_EXCLUSION_FRACTION)",) $(if $(ALPHA),--alpha "$(ALPHA)",) $(if $(BOOTSTRAP_SAMPLES),--bootstrap-samples "$(BOOTSTRAP_SAMPLES)",) $(foreach effect,$(MIN_EFFECTS),--min-effect "$(effect)") $(if $(ALLOW_DEV_JOBS),--allow-dev-jobs,) $(foreach field,$(subst $(comma), ,$(POOL_OVERRIDE)),--pool-override "$(field)")

jev-export-labels:
	$(call require_var,POLICY)
	$(call require_var,OUT)
	@$(RAIDAR) jev export-labels --labels "$(or $(LABELS),$(CURDIR)/review-surface/data/jev-labels.json)" --policy "$(POLICY)" $(if $(STORE),--store "$(STORE)",) --output "$(OUT)"

jev-import-labels:
	$(call require_var,POLICY)
	$(call require_var,STORE)
	$(call require_var,LABELS_FILE)
	@$(RAIDAR) jev import-labels --store "$(STORE)" --policy "$(POLICY)" $(if $(REVIEWER),--reviewer "$(REVIEWER)",) $(if $(REVIEWER_MODEL),--reviewer-model "$(REVIEWER_MODEL)",) "$(LABELS_FILE)"

jev-export-blind:
	$(call require_var,POLICY)
	$(call require_var,STORE)
	$(call require_var,OUT)
	@$(RAIDAR) jev export-blind --store "$(STORE)" --policy "$(POLICY)" --output "$(OUT)" $(foreach trial,$(JEV_TRIALS),--trial "$(trial)") $(if $(LIMIT),--limit "$(LIMIT)",) $(foreach id,$(REQUEST_IDS),--request-id "$(id)")

jev-import-blind:
	$(call require_var,POLICY)
	$(call require_var,STORE)
	$(call require_var,ANSWERS_FILE)
	@$(RAIDAR) jev import-blind --store "$(STORE)" --policy "$(POLICY)" $(if $(REVIEWER),--reviewer "$(REVIEWER)",) $(if $(REVIEWER_MODEL),--reviewer-model "$(REVIEWER_MODEL)",) "$(ANSWERS_FILE)"

jev-supersede-labels:
	$(call require_var,POLICY)
	$(call require_var,STORE)
	$(call require_var,REASON)
	@$(RAIDAR) jev supersede-labels --store "$(STORE)" --policy "$(POLICY)" --reason "$(REASON)" $(foreach id,$(REQUEST_IDS),--request-id "$(id)") $(if $(ALL_CURRENT),--all-current,)

jev-record-split:
	$(call require_var,POLICY)
	$(call require_var,STORE)
	$(call require_var,SPLIT_ID)
	$(call require_var,CALIBRATION)
	$(call require_var,EVALUATION)
	$(call require_var,REASON)
	@$(RAIDAR) jev record-split --store "$(STORE)" --policy "$(POLICY)" --split-id "$(SPLIT_ID)" --calibration "$(CALIBRATION)" --evaluation "$(EVALUATION)" --reason "$(REASON)"

jev-verify-promotion:
	$(call require_var,POLICY)
	$(call require_var,STORE)
	@$(RAIDAR) jev verify-promotion --store "$(STORE)" --policy "$(POLICY)"

jev-promote:
	$(call require_var,POLICY)
	$(call require_var,STORE)
	@$(RAIDAR) jev promote --store "$(STORE)" --policy "$(POLICY)" $(if $(CALIBRATION),--calibration "$(CALIBRATION)",) $(if $(EVALUATION),--evaluation "$(EVALUATION)",) $(if $(DRY_RUN),--dry-run,) $(if $(ALLOW_SCORER_BUMP),--allow-pending-scorer-bump,)

jev-shadow-rubric:
	$(call require_var,SNAPSHOTS)
	$(call require_var,STORE)
	@$(RAIDAR_SECRETS) jev shadow-rubric --snapshots "$(SNAPSHOTS)" --store "$(STORE)" $(if $(SPECS),--specs "$(SPECS)",) $(if $(REPLAY_ONLY),--replay-only,) $(if $(WITHIN),--within "$(WITHIN)",) $(if $(TRIAL),--trial "$(TRIAL)",) $(if $(OUT),--output "$(OUT)",)

# $(1) experiment file, $(2) lock name under SMOKE_LOCK_DIR, $(3) spec overrides (--set),
# $(4) trial selection (--arm). The lock and the job see the same overrides.
define smoke_run
	@mkdir -p "$(SMOKE_LOCK_DIR)"
	@$(RAIDAR) lock "$(1)" --output "$(SMOKE_LOCK_DIR)/$(2).lock.json" $(3)
	@$(TARGET_ENV) $(RAIDAR_SECRETS) run "$(1)" --lock "$(SMOKE_LOCK_DIR)/$(2).lock.json" $(3) $(4) \
		--concurrency "$(SMOKE_CONCURRENCY)" --jobs-dir "$(SMOKE_JOBS_DIR)"
endef

SMOKE_SETS = --set "analysis.attempts=$(SMOKE_ATTEMPTS)" \
	$(if $(TIMEOUT_SEC),--set "protocol.agent_timeout_sec=$(TIMEOUT_SEC)",)
AGENT_SMOKE_SETS = $(SMOKE_SETS) \
	--set "agents.0.name=$(AGENT_SMOKE_EFFECTIVE_AGENT)" \
	--set "agents.0.provider=$(AGENT_SMOKE_EFFECTIVE_PROVIDER)" \
	--set "agents.0.model=$(AGENT_SMOKE_EFFECTIVE_MODEL)" \
	--set "agents.0.reasoning_effort=$(AGENT_SMOKE_EFFECTIVE_REASONING)" \
	--set "agents.0.version='$(AGENT_SMOKE_EFFECTIVE_VERSION)'"

smoke-dry-run-check:
	@$(MAKE) --no-print-directory -n orchestrator-smoke SMOKE_ATTEMPTS="2" SMOKE_CONCURRENCY="2"
	@$(MAKE) --no-print-directory -n smoke-agents
	@$(MAKE) --no-print-directory -n smoke-agents SMOKE_ARM="codex"
	@$(MAKE) --no-print-directory -n smoke-agents SMOKE_ARM="claude"
	@! $(MAKE) --no-print-directory -n smoke-agents SMOKE_ARM="unknown" \
		>/dev/null 2>&1
	@$(MAKE) --no-print-directory -n agent-smoke AGENT="claude-code" \
		| grep -q -- '--set "agents.0.model=claude-haiku-4-5" --set "agents.0.reasoning_effort="'
	@$(MAKE) --no-print-directory -n agent-smoke REASONING_EFFORT= \
		| grep -q -- '--set "agents.0.reasoning_effort="'
	@$(MAKE) --no-print-directory -n agent-smoke \
		| grep -q -- '--set "agents.0.reasoning_effort=low"'
	@$(MAKE) --no-print-directory -n agent-smoke \
		AGENT="codex" PROVIDER="openai" MODEL="gpt-6-luna" REASONING_EFFORT="low" \
		TIMEOUT_SEC="300"

# Smoke and execute targets build the pack image first: trials bind the prebuilt
# pack tag and Harbor cannot build it. pack-build is a no-op when the tag exists.
orchestrator-smoke: docker-check pack-build
	$(call smoke_run,$(SMOKE_EXPERIMENT),orchestrator-smoke,$(SMOKE_SETS),)

agent-smoke: docker-check pack-build
	@if [ -z "$(AGENT_SMOKE_EFFECTIVE_VERSION)" ]; then \
		echo "No pack pin for agent '$(AGENT_SMOKE_EFFECTIVE_AGENT)'; set AGENT_VERSION"; \
		exit 1; \
	fi
	$(call smoke_run,$(SMOKE_EXPERIMENT),agent-smoke,$(AGENT_SMOKE_SETS),)

smoke-agents: docker-check pack-build
	$(call smoke_run,$(SMOKE_AGENTS_EXPERIMENT),smoke-agents,$(SMOKE_SETS),$(if $(SMOKE_ARM),--arm "$(SMOKE_ARM)",))

# Repository structure rules (no version in names, root and package allowlists, depth limit).
structure-check:
	@$(REPO_RUNTIME_ENV) uv run --project orchestrator python scripts/checks/check-structure.py --repo-root "$(CURDIR)"

# The step before editing a released scorer or component in place: pin the current state as
# an annotated tag so its old behaviour stays recoverable (REF=<name>@<N>, e.g. code-quality@3).
REF ?=

release-archive:
	$(call require_var,REF)
	@echo "$(REF)" | grep -Eq '^[a-z0-9][a-z0-9._-]*@[0-9]+$$' \
		|| { echo "REF must be <name>@<N> (for example code-quality@3), got '$(REF)'"; exit 1; }
	@if git rev-parse -q --verify "refs/tags/archive/$(REF)" >/dev/null; then \
		echo "tag archive/$(REF) already exists; it is never moved"; exit 1; fi
	@git tag -a "archive/$(REF)" -m "Archive of $(REF) before an in-place edit" HEAD \
		&& echo "created tag archive/$(REF) at $$(git rev-parse --short HEAD)"

quality:
	@$(MAKE) --no-print-directory structure-check
	@$(MAKE) --no-print-directory smoke-dry-run-check
	@$(MAKE) --no-print-directory experiment-locks-check
	@$(MAKE) --no-print-directory ledger-check
	@$(RAIDAR_DEV) quality gates
