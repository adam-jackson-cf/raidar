# Changelog

All notable changes to this project are documented in this file.

## [0.2.1] - 2026-02-22

- fix: align local and ci gate sync flow
- refactor: migrate execution maintenance into raidar cli

## [0.2.0] - 2026-02-22

- docs: update readme strapline

## [0.2.0] - 2026-02-22

- chore: rename project branding to Raidar

## [0.2.0] - 2026-02-22

- feat: finalize multi-provider harness support and smoke validation
- feat(orchestrator): enforce harness-model pairing
- feat(harness): add adapters for cursor copilot pi
- feat(orchestrator): enforce harness adapters and scaffold catalog
- feat(parser): implement Gemini parser and fix Claude Code multi-block handling
- feat(compliance): add structured judge response parsing with retry logic
- feat(config): add centralized configuration with pydantic-settings
- fix: cap void retries to one attempt
- fix: addressed various architecture smells
- fix(harness): support runtime validation plus sample run
- fix(pre-commit): exclude scaffold and generated folders from checks
- refactor: remove scaffold manifests and track workspace diffs
- refactor: finalize v1 execution model and evidence pipeline
- refactor: split run validity and performance gates across scoring pipeline
- refactor: migrate orchestrator ops to cli and untrack generated artifacts
- refactor: align harbor execution with scaffold task bundles
- refactor: require argv commands in task yaml
- refactor: split runner phases and add lizard gate
- chore: add changelog automation and root project readme
- chore: added enaible to ignore
- chore: ignore next-session
- chore: centralize quality gates
- chore(auth): load .env and document codex oauth plan
- chore(devex): add uv setup and harbor prep
- chore: delete actioned implementation plan
- chore: add pre-commit configuration with ruff and pytest
- test: add comprehensive unit test suite with 64 tests
- commit clean ahead of big refactor
- Add context note to implementation plan
- Fix validation and lint script issues
- Add list-agents and info CLI commands from impl-4
- Enhance schemas with @computed_field pattern
- Add comparison/aggregation module from impl-4
- Add vitest test infrastructure from impl-2
- Rename namespace to agentic_eval
- Add configuration matrix and run aggregation
- Add scoring system with multi-dimensional evaluation
- Add scaffold audit, schemas, task definition, and CLI
- Add orchestrator Python project and Next.js scaffold template
- Add research document and implementation plan

## [0.1.0] - 2026-02-22

- Initial orchestrator and task evaluation framework baseline.
