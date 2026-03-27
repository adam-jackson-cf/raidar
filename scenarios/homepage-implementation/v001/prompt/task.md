Implement a homepage for a SaaS product landing page.

The page should include:

1. A header with logo and navigation links (Home, About, Contact)
2. A hero section with headline, subheadline, and call-to-action button
3. A features section displaying 3 features in a grid
4. A footer with copyright text

Treat the header navigation, hero copy, feature-card copy, and footer copy as verification-critical: add or update `src/test/homepage.test.tsx` so the exact `Home`, `About`, `Contact`, `Get Started`, `Build Better Products Faster`, `Everything You Need`, `Lightning Fast`, `Secure by Default`, `Team Collaboration`, and `All rights reserved` strings are asserted in tests, not only present in the component tree.

The design reference image is available at ./reference/homepage.png
Implement the page within the project's existing app, components, and theming structure.

After the first implementation pass, prioritize the required verification commands and coverage gates before any extra visual polish. Run the required checks, capture at most one screenshot sanity check, and do not spend time on cleaning up dev-only screenshot artifacts or other cosmetic refinements if any required verification command remains unrun or failing.

Keep the page compact and close to the reference proportions: a simple top bar, a centered hero with moderate headline sizing, a tightly stacked 3-card feature grid, and a visible footer within the initial desktop viewport crop. Avoid oversized hero text, excess vertical padding, or spacing that pushes the footer out of frame.
