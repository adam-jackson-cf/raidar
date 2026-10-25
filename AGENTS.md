# Repository guidance

- Do not search, read or surface files under `docs/` without explicit user consent.
  A user reference to a file there grants consent for that file.
- Use repository-root `make` targets as the public interface; discover commands with `make help`.
- Use Harbor vocabulary as defined in README's Concepts.
- Keep generated artifacts under `var/` (or `RAIDAR_VAR`), outside tracked definitions.
- Exclude `var/` and `lab/experiments/**/components/**` from source analysis and quality reviews
  unless explicitly requested; component payloads are experimental treatments.
- Keep README as the human entrypoint; use `make help` for the exhaustive command catalogue.
- Before completing repository changes, run `make quality` and report its result.
