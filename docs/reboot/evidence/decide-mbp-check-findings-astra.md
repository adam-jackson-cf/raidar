F1: (a) (high confidence).
Change: pin host uv with `uv self update 0.10.0`; pin all three CI `setup-uv` steps; add a quality guard matching all nine resolver pins.
`required-version = "==0.10.0"` rejects mismatched uv for configuration-reading commands, including `run`, `sync`, and `lock`; it does not install uv.
Identity: host uv is absent from `grading_execution_digest`; the resolver pin enters problem/dependency digests. Re-pinning requires problem revalidation and experiment relocking.
Verification: observed uv 0.13.7 and three matching resolver refusals. After pinning, verify frozen sync, `make quality`, and `make security-gate TARGET=mbp`; compatibility remains untested.
F2: (a), using F3’s readiness checks (medium confidence).
Change: backup-tag mbp’s current image, then `docker save` from OrbStack and `docker load` into `raidar-mbp`; preserve committed stage evidence.
Identity: `image_lock_hash()` hashes recipe inputs, not built layers; it does not guarantee byte-identical trees. Transferring the preserved image requires no evidence replacement or relock.
Future targets must receive the preserved architecture-specific image or deliberately regenerate evidence and relock; the new client uses mbp’s image.
Verification: image inspection confirms the reported build dates and three differing layers among nine. Transfer sufficiency remains [INFERENCE].
After transfer: compare layer digests, run security/Docker gates and stage-bearing trials; do not require publishing `stage-check` to pass despite F3.
F3: (a) (high confidence).
Change: reserve publishing `stage-check` for new/changed evidence; retain existing records and use Docker/security gates plus stage-bearing trials for readiness.
Identity: documentation changes none. Floors enter the locked check digest and control failure attribution; replacing or splitting them changes evidence/locks, and changing canary schema/runtime changes `primitive_code`.
A writer-only comparison that preserves existing floors need not change primitive identity; silently accepting different floors would not validate those new measurements.
Verification: `_record()` measures floors; `_publish_records()` compares complete bytes; `load_evidence()` hashes the complete record. Different measurements therefore necessarily conflict.
Handoff: specify uv 0.10.0 and image transfer; qualify “identical bytes are a no-op”—not repeatable stop-canary checks. No files edited or containers touched.
