import { test, expect } from '@playwright/test';

// K3: the synthetic fixture's synthetic/policy-mix@1 family compares an
// experiment scored under scb-canonical-oneshot@1 with a scenario-epoch
// experiment (make review-fixture-synthetic), so every row in it carries the
// headline-policy-mismatch finding.
const FAMILY = 'synthetic/policy-mix@1';

test('a family mixing headline policies warns on each compared experiment', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('Best delivery').first()).toBeVisible();
  const family = page.locator(`[id="family-${FAMILY}"]`);
  await expect(family).toBeVisible();
  const rows = family.locator('tbody tr');
  await expect(rows.first()).toBeVisible();
  await rows.first().click();
  await expect(family.getByText('Headline policies differ in this comparison').first()).toBeVisible();
  await expect(family.getByText(/scb-canonical-oneshot@1/).first()).toBeVisible();
});

test('scenario-epoch experiments still render beside experiment runs', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('[id="family-skill-benchmark-coding-test"]')).toBeVisible();
  await expect(page.locator('[id="family-synthetic/skill-coding@1"]')).toBeVisible();
});
