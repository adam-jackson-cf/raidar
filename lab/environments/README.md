# Runtime-pack contract

A runtime pack is Raidar's language toolchain and task binding, not an agent or a
workflow. Python is the only supported pack today. An SCB problem needs no
hand-written Raidar definition when its runtime pack provides all six capabilities
below (`split-design.md` §3.2).

1. **Toolchain image.** A pinned, reproducible image supplies the runtime and agent
   CLIs. Keep scorer tools and browsers out of the agent image. The recipe lives in
   `<runtime>/pack.yaml` and `<runtime>/image/`; measurement tools are pinned in
   `<runtime>/tools.lock.yaml`. The pack id equals the experiment's `runtime`.
2. **Workspace seeding.** Before checkpoint 1, create the workspace's project
   manifest from the problem's declared dependencies and install them as the agent
   user, outside measured agent time. Keep grader dependency provisioning separate
   and offline rather than reusing the agent's environment.
   Retain provisioning evidence; a failed seed is an infrastructure error.
3. **Entrypoint substitution.** Define the snapshot-relative entry file and the
   exact command the grader runs. Import reads each pinned `checkpoint_N.md`,
   replaces only `%%%ENTRYPOINT:entry_command%%%` and
   `%%%ENTRYPOINT:entry_file%%%`, and retains all other bytes, including canaries.
   Record the command and each rendered instruction's SHA-256 in the problem pin.
   Python uses `uv run --project /app <entry>` and the pack's
   `entry_file_template: "{entry_file}.py"`.
4. **Grader runner.** Run hidden tests in a separate trusted grader sandbox, never
   in the agent's task. Normalize every case to Raidar's verifier record with its
   checkpoint origin, stable identity and status; preserve regression cases from
   earlier checkpoints. Infrastructure failure must not become a failed case or a
   zero score. Repeated reference runs determine each step's solvable set: cases
   passed in every repeat; varying cases are listed separately and excluded.
5. **Native parity runner.** Support case-by-case comparison with the dataset's
   own runner on the same reference snapshots. Record skipped parity explicitly;
   do not label it a pass. `NATIVE_RUNNER=<checkout>` enables the existing SCB path.
6. **Verify-style stage package.** Provide at least one language-appropriate
   verify package. Language-specific lint, typing and testing rules belong in that
   package, not in Raidar's stage primitives. Verify remains an optional workflow
   choice, not a condition silently added to every arm.

## Onboarding and release checks

Use the repository-root public interface (`make help` lists options):

```bash
make problem-list SOURCE=scb@<commit>
make problem-info SOURCE=scb@<commit> PROBLEM=scb/<problem>@<version>
make problem-import SOURCE=scb@<commit> PROBLEMS=a,b
make problem-validate PROBLEMS=a,b REPEATS=3 TARGET=<trusted-target>
make problem-list
```

Import and validation generate committed pin records under `lab/problems/registry/`;
materialized tasks, hidden verifier bundles and source checkouts stay under `var/`
(or `RAIDAR_VAR`). Errata and sensitivities are optional. No per-problem source edit
is part of onboarding. Nonempty solvable sets permit the corresponding validated
steps; an empty set is `excluded`, and missing evidence is not a zero solve rate.

A pack image release changes its content-addressed image tag. Task binding fields
such as entry-file templates are separate contract inputs: do not claim
that they change the image tag. Follow the `raidar-release` skill: preserve released
bytes, reimport and revalidate affected pins with native parity, refresh optional
errata bindings, then regenerate experiment locks with `make experiment-lock`.
New runtimes must supply all six capabilities before claiming this onboarding
contract; listing a future runtime in the experiment schema does not provide them.
