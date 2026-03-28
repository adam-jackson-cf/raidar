Improve the existing homepage implementation benchmark.

Goal:
- Produce a polished, production-ready homepage in the existing Next.js app that maximizes benchmark quality while preserving deterministic correctness, visual alignment to `./reference/homepage.png`, and verification stability.

Implementation requirements:
1. Keep the existing app, component, and theming structure. Implement the page within the current project surface instead of introducing a parallel rendering path.
2. Include the required homepage sections: a header with `Home`, `About`, and `Contact` navigation, a hero with a call to action, a 3-card features grid, and a footer with copyright copy.
3. Treat the header navigation, hero copy, feature-card copy, and footer copy as verification-critical. Add or update `src/test/homepage.test.tsx` so the exact `Home`, `About`, `Contact`, `Get Started`, `Build Better Products Faster`, `Everything You Need`, `Lightning Fast`, `Secure by Default`, `Team Collaboration`, and `All rights reserved` strings are asserted in tests, not only present in the rendered component tree.
4. Keep the page compact and close to the reference proportions: a simple top bar, a centered hero with moderate headline sizing, a tightly stacked 3-card feature grid, and a visible footer within the initial desktop viewport crop. Avoid oversized hero text, excess vertical padding, or spacing that pushes the footer out of frame.
5. Prioritize required verification commands and coverage/build gates before extra polish. Run the required checks after the first implementation pass, capture at most one screenshot sanity check, and do not spend time on cosmetic cleanup if any required verification command remains unrun or failing.

Definition of done:
- `bun run typecheck` passes.
- `bun run lint` passes.
- `bun run test:coverage` passes.
- `bun run build` passes.
- The homepage satisfies the verification-critical copy contract and stays visually close to the provided reference.
