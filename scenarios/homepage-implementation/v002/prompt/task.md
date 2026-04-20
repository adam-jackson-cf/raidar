Improve the existing homepage implementation benchmark.

Goal:
- Produce a polished, production-ready homepage in the existing Next.js app that maximizes benchmark quality while preserving deterministic correctness, visual alignment to `./reference/homepage.png`, and verification stability.

Implementation requirements:
1. Keep the existing app, component, and theming structure. Implement the page within the current project surface instead of introducing a parallel rendering path.
2. Include the required homepage sections: a header with `Home`, `About`, and `Contact` navigation, a hero with a call to action, a 3-card features grid, and a footer with copyright copy.
3. Treat the header navigation, hero CTA, features grid, and footer as verification-critical. Add or update `src/test/homepage.test.tsx` with semantic assertions that prove the required landmarks, interactive elements, and card count are present. Prefer role-based queries and structural checks over brittle exact-copy assertions.
4. Keep the page compact and close to the reference proportions: a simple top bar, a centered hero with moderate headline sizing, a tightly stacked 3-card feature grid, and a visible footer within the initial desktop viewport crop. Avoid oversized hero text, excess vertical padding, or spacing that pushes the footer out of frame.
5. Capture at most one screenshot sanity check after the first implementation pass, and do not spend time on cosmetic cleanup before the core implementation is stable.

Definition of done:
- The homepage satisfies the verification-critical copy contract, stays visually close to the provided reference, and reaches a clean atomic commit without bypassing verification hooks.
