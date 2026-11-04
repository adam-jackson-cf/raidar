Fix: **(d): (a) plus umask 022 at `calls._root`’s root-shell boundary** (high confidence); reject (c) as the sole remedy.
Explicitly chmod installer-created trusted directories and namespace parents to 0755; normalize after `_wrap`, including `bin/`. Do not recursively chmod unrelated ancestors.
Audit: `payload/main.py` lacks an umask; `install_tree.py` depends on it for parents, `bin/`, staging HOME and pre-normalization writes.
Audit: `supervise.py` uses private 0700 directories and already sets child umask 022; `walk.py` creates archives explicitly 0600; `isolate.py`/`__init__.py` create nothing.
Audit: `calls.py`’s `mkdir -p -m 0755` protects only the terminal directory: canary intermediate parents remain umask-dependent; call/spec directories and closed-flag leaves have explicit protection.
Audit: `gate.py` already prefixes commands with umask 022; `gate_tool.main()` also sets it. Gate/seal scratch and records therefore do not inherit exec’s 0000.
Audit: `/app/.raidar` package state/output channels are agent-owned, created through UID-1000 calls with umask 022—not trusted root evidence; host records remain separate.
Evidence: disposable images reported run umask 0022 on both targets; the actual installer under forced 0000 on mbp produced namespace/bin 0777, versus 0755 under 0022.
Identity cascade: **all nine** digests change: `frame/1`, `install/1`, `setup/1`, `gate/1`, `stop/1`, `outputs/1`, `acceptance-reply/1`, `jev-judge/1`, `runner/1`.
`workflow/identity.py::_files` includes every payload module in every inventory, including adapter-independent stage-check identities.
Regenerate stage-check evidence for define, verify and docs-check; refresh define’s primitive-bearing record and remeasure all installed-tree records; regenerate every tracked experiment lock, including one-shot.
No new package/workflow `@N` releases are required for this primitive-only repair: their content fingerprints exclude primitive code; preserve frozen `lab/releases.yaml` entries.
Executed `grading_sources()` returned **no `subject/primitive` modules**: this repair alone changes neither grading identity nor the nine problem validations; mbp grader repins remain separate.
Sequencing: land a separately reviewed primitive/identity PR first, including evidence and relocks; then rebase the mbp-readiness PR and regenerate its affected locks.
Do not declare mbp ready beforehand; require passing security/quality gates and a real-agent smoke afterward. Problem revalidation and the oracle trial do not exercise this boundary.
Severity: **High—agent-writable trusted tool installation**, not demonstrated root escalation. UID 1000 successfully renamed/replaced `<namespace>/<name>` in the disposable probe.
Current locked trials reject the initial installed-tree mismatch before an agent turn (`package_trial.install_locked`); persistent substitutions trigger owner/digest checks and invalidation through workflow health.
**T1 does not cover this tree**: `health/tool_tree.py` watches `/opt/raidar-tools`, not `/opt/raidar/stages`; gate/call checks and W1/W4/W11 provide the relevant detection.
Detection is not complete prevention: the probe restored the original root-owned tree and obtained an identical digest with no violation; transient substitution can escape sampled checks.
No repository files changed; no `native-*` containers touched.
