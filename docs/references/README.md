# Documentation Source of Truth

`docs/references` is the canonical documentation set for this repository.

Rules:
- Keep implementation and operational docs in this directory.
- Treat docs outside this directory as non-authoritative and remove duplicates.
- Keep task prompt/rule markdown under `tasks/**` because they are runtime task assets, not general docs.
- Keep `/Users/adamjackson/Projects/typescript-ui-eval/analyze-results.md` at repo root because it is an operational prompt artifact, not a documentation page.
- Keep `AGENTS.md` at repo root because it is execution policy, not product documentation.

## Reference Index

- `docs/references/orchestrator-usage.md`: setup, CLI usage, prerequisites, artifact lifecycle.
- `docs/references/orchestration-flow.md`: run lifecycle and artifact topology.
- `docs/references/new-task.md`: task authoring and versioning guide.
- `docs/references/new-agent.md`: adding a harness/agent guide.
- `docs/references/OAuth-improvement.md`: Codex OAuth roadmap and implementation status.
- `docs/references/architecture-review.md`: current architecture state and risks.
- `docs/references/agentic-eval-system-research.md`: background research and rationale mapped to the current architecture.

Prompt artifacts:
- `/Users/adamjackson/Projects/typescript-ui-eval/analyze-results.md`: deterministic analysis prompt template for latest suite outputs.
