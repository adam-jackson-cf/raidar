You are improving a homepage implementation to maximize benchmark quality on codex-cli using codex/gpt-5.4-mini-low.

Goal:
- Produce a polished, production-ready homepage HTML output via `src/homepage.ts` that scores at or above 88.05643109423825 while preserving deterministic correctness and verification stability.

Constraints:
- Follow AGENTS.md behavioral rules.
- Keep the solution dependency-light and compatible with Bun built-ins.
- Do not break existing lint, tests, or verify gates.

Implementation requirements:
1) `renderHomepage()` must return a complete HTML document string with semantic landmarks (`header`, `main`, `footer`).
2) Include clearly differentiated sections with ids: `hero`, `features`, `proof`, and `cta`.
3) Provide one primary call-to-action element using `data-testid="primary-cta"`.
4) Homepage copy must be concrete, concise, and user-value focused (avoid placeholders and filler).
5) Keep accessibility in mind: meaningful heading hierarchy, link/button labels, and readable structure.

Definition of done:
- `bun run lint` passes.
- `bun run test` passes.
- `bun run verify` passes.
- Acceptance checks and rubric criteria are satisfied with strong implementation quality.
