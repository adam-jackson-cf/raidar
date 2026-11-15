# Contributing

A contributor guide that takes repository changes through local gates and registered releases.

| Does                                                                                                                        | Does not                                                                                 |
| --------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| **Gates:** checks changes before commit and in CI.<br/>**Releases:** preserves prior identities and pins distributed bytes. | **Contract build:** never publishes artifacts.<br/>**Checks:** never fix or stage files. |

## 1 · Gate a change

_Local gates select affected seams; CI runs the full slow and quality tiers._

```mermaid
flowchart LR
C["<b>Change</b><br/>Working tree"]:::human --> P{"<b>Policy valid?</b><br/>Whole tree"}:::det
  P -->|Yes| Q{"<b>Quality passes?</b><br/>Static and fast"}:::det -->|Yes| S{"<b>Seams pass?</b><br/>Local affected; CI full"}:::det
  P -->|No| X["<b>Stop and repair</b>"]:::block
  Q -->|No| X
  S -->|No| X
  S -->|Yes| A["<b>Gate passes</b>"]:::gate
  classDef det fill:#d0f0ea,stroke:#16796b,color:#18202b
  classDef human fill:#ffe0d5,stroke:#ad5335,color:#18202b
  classDef gate fill:#dcefd3,stroke:#467b32,color:#18202b
  classDef block fill:#ffdddd,stroke:#b73d42,color:#18202b
```

```bash
uv run --project orchestrator --extra dev pre-commit install --hook-type pre-commit
make gate-commit
# CI uses: make gate-ci
```

| Change                 | Automated evidence                                                                                          | Contributor action                                                                                          |
| ---------------------- | ----------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Tests and capabilities | Ownership and tier checks block changes that fail their requirements.                                       | Declare each new capability's owner and each test file's tier.                                              |
| Python and analysis    | Ruff, strict mypy, import contracts, complexity, policy and I/O boundary checks block every finding.       | Use owning seams and fix each finding; there are no baselines or waivers.                                   |
| Toolchain              | Exact tool-pin, installer/lock agreement and maintained Markdown/YAML formatting checks block changes that fail their requirements. | Use the locked formatter.                                                                                   |
| Dependencies           | Every commit and CI run audits every lock file the git inventory holds outside fixture programs; every advisory blocks, fixable or not. | Upgrade and relock; a lock format without an auditor or a manifest without a lock is a finding.            |

> [!IMPORTANT]
> **Owning policies:** [ownership map](../../scripts/checks/repository-owners.json) defines owners, tiers and seams; Ruff, strict mypy, Prettier, the dependency audit and the analyzer I/O boundary check have no baselines and block on every finding. Unknown local ownership runs all T2 seams rather than skipping tests. Docker, performance and security checks are separate gates.

> [!NOTE]
> **Dependency audit:** `uv.lock` goes through the pinned pip-audit, `package-lock.json` through `npm audit`. Every audit line starts with `SECURITY.dependency-audit`: an advisory line names lock, OSV ecosystem, package, exact version, advisory ID, aliases and fix versions; any other finding is a `failure=` line (`unknown-lock-format`, `manifest-without-lock`, `exception`, `auditor`). The [checker](../../scripts/checks/check-toolchain-parity.py) documents the exact format. Only an owner-approved entry in [dependency-audit-exceptions.json](../../scripts/checks/dependency-audit-exceptions.json) suppresses one advisory, and only while no fixed release exists; expired, stale or fixable entries are findings.

Sources: [.pre-commit-config.yaml](../../.pre-commit-config.yaml); [Makefile](../../Makefile) (`gate-commit`, `gate-ci`, `quality`); [scripts/checks/repository-owners.json](../../scripts/checks/repository-owners.json); [scripts/checks/check-python-quality.py](../../scripts/checks/check-python-quality.py); [scripts/checks/check-analyzer-scope.py](../../scripts/checks/check-analyzer-scope.py); [scripts/checks/check-toolchain-parity.py](../../scripts/checks/check-toolchain-parity.py) (`audit_findings`).

## 2 · Find the owning source

_Follow enforced package boundaries instead of adding another extension path._

- **Import direction:** `cli` → `evaluation` → `engine` → `design` → `scoring` → `grading` → `subject` → `judge` → `contract`; consult import contracts for subtiers.
- **Extensions:** author [stage packages](../../lab/stages/README.md) and [runtime packs](../../lab/environments/README.md), not stage types in the orchestrator.

- **Lab:** `experiments/`, `problems/`, `agents/`, `workflows/`, `stages/`, `environments/`, `judge/` and `ledger/` hold tracked definitions.
- **Decisions:** [binding design](../../split-design.md), [ADRs](../adr/) and [reboot evidence](../reboot/) retain normative and historical records.
- **Review UI:** [development and projection contract](../../review-surface/README.md) live with the surface.

Sources: [split-design.md](../../split-design.md) (`sections 1,5,7`); [orchestrator/.importlinter](../../orchestrator/.importlinter) (`tiers`); [orchestrator/src/raidar/contract/paths.py](../../orchestrator/src/raidar/contract/paths.py); [review-surface/README.md](../../review-surface/README.md).

## 3 · Update releases in place

_Edit the definition in place and update its registered digest; relock and rerun affected experiments._

- **Agent, workflow or stage:** edit in place and update the registered digest of its existing `@N` in `lab/releases.yaml` (`make releases-check` prints the line); update dependent workflow digests the same way. `make release-archive REF=<name>@<N>` is optional history.
- **Stage evidence:** run `stage-check NAME=<namespace/name>` for the current installed tree and declared stop/output obligations.
- **Application version:** fetch origin, branch from `origin/main`, then run `make version-release`; commit its three generated files in the printed release subject through normal gated review. Regenerate after rebasing onto newer main.
- **Version-only release:** does not require experiment relocks; execution toolchains pin result-affecting versions separately.
- **Invalidating code change:** rerun affected experiments. Experiment locks and problem validations still pin grader and `primitive_code` digests and refuse mismatches: relock through `make experiment-lock`, re-validating any problem RT7 refuses, then rerun. There is no repository identity record or revalidation ceremony; `job.json` retains `runner.commit` and `runner.dirty`.

Sources: [Makefile](../../Makefile) (`release-archive,version-release`); [split-design.md](../../split-design.md) (`5.6; 9 · Rules for every phase`); [lab/stages/README.md](../../lab/stages/README.md); [scripts/release/bump-version.py](../../scripts/release/bump-version.py); [orchestrator/src/raidar/design/experiment/lock.py](../../orchestrator/src/raidar/design/experiment/lock.py) (`execution_section, seal_lock`); [runtime_gate.py](../../orchestrator/src/raidar/design/experiment/runtime_gate.py) (`_binding_validation_issues`); [primitive/registry.py](../../orchestrator/src/raidar/subject/primitive/registry.py).

## 4 · Build the portable contract

_Distribute exact wheel and sdist bytes, not a build from a Git tag._

```bash
make contract-build CONTRACT_DIST_DIR=var/contract/v<version>
# In the consumer environment with the exact hash-pinned artifact installed:
python -m raidar_contract.conformance.check
```

- **Build:** refuses existing output, writes wheel/sdist and prints their SHA-256 hashes; publication is a separate owner action.
- **Artifacts:** private releases live under `var/contract/v<version>/`; [`contract/releases/`](../../contract/releases/) supplies exact-version/hash requirements.
- **Install:** require the recorded version and hash; a tag does not establish installed-byte identity.
- **Conformance:** portable parse/refusal/digest vectors run without Raidar or Harbor; package dependencies are Pydantic and PyYAML.

> [!IMPORTANT]
> **Distribution ≠ contract id:** `raidar-contract 2.0.0` separates lab policy while retaining `stage/1`, workflow schema 2, `criteria/1`, `stage-tree/1` and `agent/1`. Contract and package identity rules remain in the [authoring contract](../../lab/stages/README.md).

Sources: [split-design.md](../../split-design.md) (`section 5.8`); [contract/pyproject.toml](../../contract/pyproject.toml); [Makefile](../../Makefile) (`contract-build`); [contract/conformance/check.py](../../contract/conformance/check.py); [contract/releases/1.0.0.requirements.txt](../../contract/releases/1.0.0.requirements.txt); [contract/releases/1.1.0.requirements.txt](../../contract/releases/1.1.0.requirements.txt); [contract/releases/2.0.0.requirements.txt](../../contract/releases/2.0.0.requirements.txt); [`contract/src/raidar_contract/__init__.py`](../../contract/src/raidar_contract/__init__.py).
