Use the project's section/ui structure.
Do not use inline styles.
Use the project's global theming tokens and shared UI primitives to impart the design.
A task is complete only when all required quality gates are green.

- **ALWAYS** use Conventional Commits (`feat|fix|refactor|build|ci|chore|docs|style|perf|test`).
- **ALWAYS** use an atomic commit pattern at the logical conclusion of tasks to establish safe check points of known good.
- **ALWAYS** start with `git status`, `git diff`, `git log` before edits.
- **NEVER** run `git push`.
- **NEVER** run `git checkout` or `git switch`.
- **NEVER** run `git reset --hard`, `git clean`, `git restore`, or `rm`.
- **NEVER** run repo-wide search/replace scripts (e.g. `sed -i`, `perl -pi -e`, `python -c`).
- **ALWAYS** use the repo’s package manager/runtime (no swaps).
