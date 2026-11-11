// Journey: Compare -> Explain. A reviewer reads a spec's verdict on the Experiments page, opens
// where its points were lost, and follows "Start with" to the trial worth reading first.
// Run: node scripts/surface/lever.mjs --data-dir <empty dir> --evidence-dir <dir> \
//        --journey review-surface/journeys/compare-experiments.journey.mjs
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { surface } from '../../scripts/surface/lever.mjs';

test('the Experiments page explains a verdict and hands off to the trial worth opening', async () => {
  const lever = surface();
  const outcome = await lever.withPage(async (page, { errors }) => {
    await page.goto('/');
    const families = page.locator('nav[aria-label="Problem families"] a');
    await families.first().waitFor();
    const familyCount = await families.count();
    assert.ok(familyCount >= 2, `expected at least two problem families, saw ${familyCount}`);

    const verdict = page.getByText(/delivered to spec \(\d\.\d\d\)/).first();
    await verdict.waitFor();
    await page.locator('tbody tr').first().click();
    await page.getByText(/Where points were lost/).first().waitFor();
    await page.getByText(/What held up/).first().waitFor();

    const startWith = page.locator('span', { hasText: /^Start with/ }).first();
    await startWith.waitFor();
    const startText = (await startWith.textContent()).trim();
    const link = startWith.getByRole('link');
    const href = await link.getAttribute('href');
    await link.click();
    await page.waitForURL(/\/runs\/.+/);
    await page.getByText('Why it scored this').waitFor();
    assert.deepEqual(errors, []);
    return { families: familyCount, start_with: startText, opened: href };
  });
  lever.record(outcome);
});
