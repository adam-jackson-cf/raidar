Decision: **a** (high confidence, about 0.85). Move the four validated grader images to `mbp` byte for byte. Option b re-derives the pins on each machine at about 6 h per machine. Option c removes the guarantee the design requires.
- **Facts checked:** source in the working tree matches `c9c2fd42`. This machine's OrbStack (containerd store, arm64) holds all four tags, and their IDs equal the pins. The tracked locks and validation files pin only these four; tag `963c14e63ff5e15d` appears 19 times. A missing image tag rebuilds at `grader_image.py:132`, and the new ID is refused at `verifier.py:154-160`.
- **Why rebuilds differ:** each ID is an image-index digest. That index includes a build provenance attestation, so a rebuild cannot reproduce the ID.
- **Size:** `docker save` of `963c14e63ff5e15d` writes an `index.json` naming exactly `sha256:1989940c…`. All four images save to 654 MB combined, because they share layers.
- **Why not c:** the tag is already a hash of the build recipe (the grader Dockerfile, including its lock digests). The content ID was pinned because the same recipe does not produce the same bytes. The design hashes bytes (§5.4), requires identity to be reproducible offline (§3.2), holds the hidden grader fixed (§7.3) and refuses at runtime (phase 4b). Pinning the recipe would weaken that guarantee. Reproducible builds would be a separate identity change and would need revalidation.
- **Is a loaded image the validated grader?** Yes, if the IDs match. The index digest covers every manifest, config and layer digest, and the daemon checks each blob's digest on load. A matching ID on `mbp` is stronger evidence than any rebuild. Neither a nor b covers differences in `mbp`'s kernel or OrbStack version.
- **Procedure:**
  1. Now, before `~/OrbStackTemp` is lost: `docker --context orbstack save -o <durable>/oracle-graders.tar` with the four tags. Keep this file off the temporary OrbStack, because it becomes the only source.
  2. Run `env -u AGENT make target-up TARGET=mbp`. Confirm `docker --context raidar-mbp info` shows `io.containerd.snapshotter.v1` and `aarch64`. A classic image store reports a different kind of ID, which can never equal the pins.
  3. Run `docker --context raidar-mbp load -i …tar`. This points the four tags at the loaded images and replaces the older `715698bf…`-era images.
- **Checks:** for each tag on `raidar-mbp`, `image inspect --format '{{.Id}}'` must equal that problem's `grader_environment_id` in its validation file. If a stored job is available, run one `make assess-regrade` on `mbp` to confirm grading behaviour. No make target for this exists today (`make help` lists none). Add `grader-export`, `grader-import` and `grader-check`, reading pins from the tracked records, in a separate code change.
- **The handoff must say:**
  1. Give the table of the four tags and their pinned IDs.
  2. Load the images and pass the check before any grading on `mbp`.
  3. Never remove these tags or run `image prune -a`. A missing tag triggers a rebuild with a new ID, which is refused; recover by loading the archive again.
  4. Keep the archive file.
  5. These images are arm64 only, so an amd64 target needs option b.
  6. A change to the grader implementation digest needs revalidation anyway.
- **Risks:**
  1. `mbp` was unreachable in the earlier readiness check, so create the archive first.
  2. Losing the archive leaves only option b.
  3. I have not checked how `mbp`'s Docker version handles the nested index on load. The ID check would catch a problem.
  4. This checkout's local `main` (`7481f01f`) is older than `origin/main` (`c9c2fd42`).
