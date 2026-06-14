import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.REVIEW_SURFACE_PORT || 5950);
const BASE = `http://localhost:${PORT}`;

// End-to-end functional regression net for the review surface. Runs against the
// built SPA + API served by server.mjs over the projected synthetic fixture.
// Requires `make review-surface-data` and `npm run build` first (the
// `make review-surface-test` target does both).
export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  reporter: [['list']],
  use: {
    baseURL: BASE,
    headless: true,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'node server.mjs',
    url: `${BASE}/api/runs`,
    reuseExistingServer: true,
    timeout: 30_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
