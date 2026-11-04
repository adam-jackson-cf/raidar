Decision: **b (medium-high confidence)**. Option (c), as the brief describes it, cannot be built. On the old machine's containerd store, `docker image inspect` never reports the config digest.
- **Verified differences:** For `raidar-oracle-grader:963c14e63ff5e15d`, `raidar-mbp` (classic store, Docker 29.8.2) reports `.Id` as `e0044b07…`, the config digest. `orbstack` (containerd store, 29.4.0) reports `1989940c…`, the index digest. `orbstack --platform linux/arm64` gives the manifest digest `cf944c2a…`. The containerd output has no config-digest field.
- **Verified agreement:** Both stores report identical `RootFS.Layers` (the ordered layer content hashes) and identical `.Config`, `.Architecture`, `.Os` and `.Created`. For tag `963c14e63ff5e15d` the `RootFS` hash is `f11145c1…` on both. I checked all four pinned tags: `963c14…`, `81cc25…`, `4d9f31…` and `2cf4f3…`. All match.
- **Why not (c):** A store-independent ID would need a new synthetic digest built from those fields. That means an identity code change and a reviewed commit, plus the same 6 h revalidation. It adds work and keeps the same cost as (b).
- **Why not (d):** Switching the image store changes a shared host and risks the `native-*` containers.
- **Procedure:**
  1. Run `env -u AGENT make problem-validate PROBLEMS=all TARGET=mbp REPEATS=5` for the nine problems.
  2. Check that the new validation records pin `mbp` IDs, for example `963c14…` → `sha256:e0044b07…`.
  3. Run `make experiment-lock` on every lock that pins a grader ID.
  4. Run `make quality`.
- **Handoff impact:** After relocking, `refuse_grader_mismatch` refuses graded trials on containerd-store hosts such as the old OrbStack. Rebuilding the images or moving `mbp` to the containerd store requires another revalidation. `grader_implementation_sha256` (`a93cc5f7…`) stays the same.
- **Unrelated stale image:** `mbp` also has a different, older `raidar-oracle-grader:d748dc23e914d01c` (`7c07336f…`, created 2026-10-27; OrbStack has `cb939137…`, 2026-10-31). No validation pins that tag, so it does not block (b).
- I edited nothing. Every Docker command was read-only (`image inspect`, `image ls`, `info`, `version`).
