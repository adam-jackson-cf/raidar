Decision: a (high confidence) — transfer the exact validated images; retain existing pins and mismatch refusal.
Verified: all four local images match committed validation IDs: `1989940cc347`, `e8210c89bfc2`, `49fe02e6cda1`, `752528685e4b`; all are `linux/arm64`.
Practicality: an actual four-image `docker save` stream measured **654,363,648 bytes (~624 MiB)**; individual image sizes are 632–645 MB.
Proposed procedure: archive those images with a SHA-256 checksum on durable storage before retiring `~/OrbStackTemp`; do not depend on reconnecting its drive.
Then establish the SSH tunnel with `env -u AGENT make target-up TARGET=mbp` and transfer:
`set -o pipefail; docker --context orbstack save raidar-oracle-grader:{963c14e63ff5e15d,81cc253926b5099e,4d9f312562ca60cb,2cf4f359dd32f0b6} | docker --context raidar-mbp load`
Checks: compare every destination tag’s **full** `.Id` with all nine validation records and applicable locks; require `linux/arm64` before grading.
`save/load` preserves image configuration and layers—not merely the recipe—so matching content IDs preserve the validated-image evidence; archive serialization need not be byte-identical.
Handoff: record archive location/checksum, tag→full-ID mapping, target configuration, and “load, never rebuild”; loading replaces older tag mappings.
Host identity also requires matching source/runtime bytes and dependencies: verified Python `3.13.7`, packaging `26.0`, pydantic `2.12.5` produce pinned digest `a93cc5f7…`.
Pack identity excludes its filesystem location; installed-tree evidence uses container architecture. Preserve arm64 evidence and run target readiness/smoke checks after transfer.
Reject c: recipe equality does not establish byte equality; `split-design.md` §§3, 5.4, 5.8, 6 require generated validation evidence, identity enforcement, and health checks.
Use b only if exact images become unavailable: revalidate all nine with five repeats and relock through public make targets; never hand-edit pins.
Limits: the historical report confirms different IDs, not current mbp contents. Checkout `dbafa71d` differs from stated main, but inspected identity inputs/pins match `c9c2fd42`.
No files edited; no Docker commands contacted mbp; destination identity and readiness remain unverified.
