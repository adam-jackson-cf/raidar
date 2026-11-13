# Repository guidance

- `split-design.md` (repository root) is the binding design: read it before changing workflows,
  stages, primitives, agents, problems, health, scoring or identity. Stages are packages under
  `lab/stages/` on Raidar-owned primitives; never add a stage type in `orchestrator/src`.
- Use repository-root `make` targets as the public interface; discover commands with `make help`.
- Use Harbor vocabulary as defined in README's Concepts.
- Keep generated artifacts under `var/` (or `RAIDAR_VAR`), outside tracked definitions.
- Exclude `var/` from source analysis and quality reviews unless explicitly requested.
- Keep README as the human entrypoint; use `make help` for the exhaustive command catalogue.
- Before completing repository changes, run `make quality` and report its result.
- Tests use the tiers declared as pytest markers in `orchestrator/pyproject.toml` and per file in
  `scripts/checks/repository-owners.json`.
