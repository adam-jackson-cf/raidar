## Design Guidance

- **ALWAYS** use the project's section and shared UI structure.
- **ALWAYS** use the project's global theming tokens and shared UI primitives to impart the design.
- **NEVER** use inline styles.

## Completion Discipline

- **ALWAYS** treat the task as incomplete until the atomic commit succeeds and all required hook-enforced and explicit quality gates are green; stay in a fix-and-retry loop for any failing or partial result, and **NEVER** claim completion, hand off, or stop on partial verification alone.

## Git Workflow

- **ALWAYS** use Conventional Commits (`feat|fix|refactor|build|ci|chore|docs|style|perf|test`).
- **ALWAYS** use an atomic commit pattern at the logical conclusion of tasks to establish safe check points of known good.
- **ALWAYS** start with `git status`, `git diff`, `git log` before edits.
- **NEVER** run `git push`.
- **NEVER** run `git checkout` or `git switch`.
- **NEVER** run `git reset --hard`, `git clean`, `git restore`, or `rm`.
- **NEVER** run repo-wide search/replace scripts (e.g. `sed -i`, `perl -pi -e`, `python -c`).
- **ALWAYS** use the repo’s package manager/runtime (no swaps).
