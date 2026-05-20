# Optimise Harbor Browser Image

## Context

The homepage benchmark currently needs a browser primarily to capture screenshots for visual comparison against the reference image. Future scenarios may need richer user journeys across a web app, but the immediate requirement is deterministic page load plus screenshot capture.

Recent runs exposed a set of runtime and image-build issues after moving from the previously warm OrbStack external data store to a fresh local fallback store.

## Timeline

- March 31, 2026: The last clean `homepage-implementation/v002` Spark run completed in about 99 seconds. The run had warm baseline/preflight/image state, and fast-image prep took about 8 seconds.
- April 9, 2026: OrbStack logged an external-drive BTRFS I/O failure and the VM stopped. The configured Docker data dir was `/Volumes/UGreen-External/Docker`.
- April 10, 2026: OrbStack could not start because the external drive was unavailable, so a local fallback data dir was introduced.
- April 10, 2026 onward: The local fallback store was cold. Docker image cache, browser layers, apt layers, and package caches all had to be rebuilt.

## Root Causes Identified

- The repo should be Docker-compatible-engine agnostic, but Docker Desktop and OrbStack can still differ in cache state, storage performance, networking, and BuildKit/buildx behavior.
- The external OrbStack data-dir failure reset the effective Docker cache state when switching to the local fallback.
- Fast image builds were happening before Harbor execution, so stalls could appear as Harbor timeouts even before a Harbor trial existed.
- Dead image-cache lock directories from aborted runs could block later runs until stale-lock timeout.
- Starter preflight was doing `bun install --frozen-lockfile` work in disposable per-run workspaces instead of a persistent baseline workspace.
- The heavy visual/browser layers were being invalidated too often because browser/system dependency installation happened after app source copy in the Dockerfile.
- Buildx was observed hanging under local OrbStack storage, while classic `docker build` progressed on the same Dockerfile/context.
- Playwright browser installation is the dominant cold-build cost for visual scenarios.

## Decisions

- Raidar should remain Docker-engine agnostic. Public surfaces should rely on Docker-compatible behavior, not OrbStack-only or Docker-Desktop-only behavior.
- Remote browser services such as Browserbase are not a viable default for this repo because runners must be self-contained.
- The active Docker data dir should be treated as a warmable target. Local fallback storage can become warm just like the external store, but it does not inherit external-store layers automatically.
- The short-term path should optimize the existing self-contained runner image flow rather than replacing the visual stack immediately.
- The strategic default should be a prewarmed browser base image for visual scenarios so the task image does not install Chromium/Playwright browser dependencies on every cold build.
- A separate lightweight screenshot-only backend can be considered later, but it should not block stabilizing the current Playwright-based path.

## Alternatives Considered

### Browserbase / Stagehand

- Good for hosted agentic browser workflows.
- Reduces local browser/runtime overhead by moving browser execution off-machine.
- Rejected as the default path because the runner must be self-contained and not rely on remote services.

### Browser Use

- Good agentic browser automation surface.
- Local mode still depends on browser runtime installation.
- Cloud/browser-hosted mode is not viable as the default for the same reason as Browserbase.

### Puppeteer Core

- Viable lightweight self-contained option when paired with a system browser or preinstalled Chrome/Chromium.
- Lower framework overhead than Playwright for screenshot capture.
- Still needs a browser binary and careful journey support if future scenarios become more interactive.

### chromedp / headless-shell

- Strongest lightweight option for screenshot-only capture.
- Minimal browser-control layer via Chrome DevTools Protocol.
- Less ergonomic for future multi-step app journeys and less aligned with current TypeScript/Playwright-style test ergonomics.

### Prewarmed Playwright Browser Base

- Best near-term compromise.
- Keeps the current screenshot/journey-capable model.
- Avoids per-task `playwright install chromium`.
- Larger image, but warmed once per Docker data dir and reused by Harbor task images.

## Required Work

### 1. Add a Public Warm-Up Surface

Add a root `make` target that warms the active Docker data dir, regardless of whether the engine is Docker Desktop or OrbStack.

Candidate command:

```bash
make docker-warm
```

Expected behavior:

- Verify `docker info` works.
- Build or pull the reusable browser base image.
- Build the standard fast task images needed by smoke/homepage workflows.
- Print the active Docker context and image tags warmed.
- Fail clearly if Docker is unavailable.

The command must not assume OrbStack. It should operate against whichever Docker context is active.

### 2. Introduce a Reusable Browser Base Image

Add a repo-owned browser base image path for visual scenarios.

Options:

- Use an official Playwright Docker image as the base.
- Or build a slimmer image from `oven/bun:1` with only required screenshot dependencies and Chromium/headless shell.

Initial recommendation:

- Start with the Playwright browser base because it is lower risk and supports future user journeys.
- Measure image size and build time.
- Only move to `chromedp/headless-shell` if image size remains unacceptable.

### 3. Update Harbor Task Image Build

For visual scenarios:

- Inherit from the reusable browser base image.
- Avoid `bunx playwright install chromium` in the per-task Dockerfile.
- Keep app-specific layers after browser/system layers so source changes do not invalidate the expensive browser stack.

For non-visual scenarios:

- Keep the lighter base path.

### 4. Preserve Engine Agnosticism

Audit and harden these assumptions:

- Do not rely on OrbStack-specific paths, sockets, or CLI behavior inside repo code.
- Do not require Docker Desktop-specific Compose/buildx behavior.
- Avoid buildx-only flags for core task-image builds unless explicitly required.
- Keep `docker info`, `docker build`, `docker run`, and Docker image labels as the core compatibility surface.

### 5. Improve Diagnostics

Keep or improve:

- Pre-Harbor fast-image build logs.
- Explicit timeout classification for fast-image builds.
- Dead-owner cache-lock reclamation.
- Polling output for experiment/run/Harbor phase visibility.

### 6. Add Validation

Add tests and smoke coverage for:

- `make docker-warm` dry-run or mocked behavior.
- Browser base image selection for visual vs non-visual scenarios.
- Dockerfile layer order so browser dependencies stay before app source copy.
- Fast image cache hits after warm-up.
- Clean failure when Docker daemon is unavailable.

## Current Interim Tools

Local helper commands were added for Docker data-dir switching and run polling:

```bash
orbstack-use-auto
orbstack-use-local
orbstack-use-external
orbstack-data-status
raidar-harbor-poll <experiments-root> [filter]
```

These are useful locally but should not become required public repo surfaces. The public repo surface should be root `make` targets.

## Success Criteria

- A fresh local Docker data dir can be warmed with one public command.
- The homepage visual scenario no longer pays browser-install cost on each task image build.
- The warm-up path works with Docker Desktop and OrbStack using the active Docker context.
- Harbor reaches trial execution reliably after warm-up.
- Screenshot capture still produces deterministic visual artifacts for comparison.
- The path leaves room for future user-journey testing without replatforming immediately.

## Open Questions

- Is the official Playwright Docker image size acceptable once cached locally?
- Should Raidar support a separate screenshot-only backend using `headless-shell` for fast visual-only scenarios?
- Should visual scenarios pin browser versions independently from package versions?
- Should image warm-up become part of `make env-setup`, or stay as an explicit `make docker-warm` command?
