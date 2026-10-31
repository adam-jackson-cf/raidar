argv: opus (high) — return declared `run` unchanged; Astra is safe but unnecessary.
`gate_tool.py:369-383,501-508` rebuilds the environment and resolves against explicit package PATH; `install.py:95-103` puts root-owned wrappers first. Workspace or inherited PATH cannot shadow them.
`payload/supervise.py:102-115` executes checks in child processes: conftest cannot alter the parent’s environment.
Setup/stop likewise forward declared argv through `site.py:153-162`; this matches `lab/stages/README.md:57,83` (commands on PATH; fixed, clean call environment).
locks: regenerate — the behavioral `gate.py` change alters transitive AST identity: `subject/workflow/identity.py:107-151,161-174`; `subject/workflow/package.py:25-32` imports it, and `runner/1` is always included.
Read-only inventory execution confirmed `gate.py` belongs to both `runner/1` and `gate/1`. `design/experiment/arm.py:124-154` stores these in arm identity; `lock.py:388-393,543-569` includes arms in execution identity. Regenerate affected experiment locks with `make experiment-lock`.
