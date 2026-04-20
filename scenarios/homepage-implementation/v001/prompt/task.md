Implement a homepage for a SaaS product landing page.

The page should include:

1. A header with logo and navigation links (Home, About, Contact)
2. A hero section with headline, subheadline, and call-to-action button
3. A features section displaying 3 features in a grid
4. A footer with copyright text

Treat the header navigation, hero CTA, features grid, and footer as verification-critical: add or update `src/test/homepage.test.tsx` with semantic assertions that prove the required landmarks, interactive elements, and card count are present. Prefer role-based queries and structural checks over brittle exact-copy assertions.

The design reference image is available at ./reference/homepage.png
Implement the page within the project's existing app, components, and theming structure.

After the first implementation pass, capture at most one screenshot sanity check and do not spend time on cleaning up dev-only screenshot artifacts or other cosmetic refinements before the core implementation is stable.

Keep the page compact and close to the reference proportions: a simple top bar, a centered hero with moderate headline sizing, a tightly stacked 3-card feature grid, and a visible footer within the initial desktop viewport crop. Avoid oversized hero text, excess vertical padding, or spacing that pushes the footer out of frame.
