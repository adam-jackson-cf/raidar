import { test, expect, type Page } from '@playwright/test';

// Deterministic ids from the synthetic fixture (make benchmark-fixture-synthetic).
const FAILING_RUN = 'synthetic-bugfix-gpt-5.5-low-04';
const UNSCORED_RUN = 'synthetic-bugfix-gpt-5.5-low-05';
const PASSING_RUN = 'synthetic-bugfix-gpt-5.5-medium-01';

async function openExperiments(page: Page) {
  await page.goto('/');
  await expect(page.getByText('Best delivery').first()).toBeVisible();
}

test.describe('navigation', () => {
  test('moves between Experiments and Runs', async ({ page }) => {
    await openExperiments(page);
    await page.getByRole('navigation').getByText('Runs').click();
    await expect(page).toHaveURL(/\/runs$/);
    await page.getByRole('navigation').getByText('Experiments').click();
    await expect(page).toHaveURL(/\/$/);
  });

  test('scenario anchors jump to a family', async ({ page }) => {
    await openExperiments(page);
    const anchors = page.locator('nav[aria-label="Scenario families"] a');
    await expect(anchors.first()).toBeVisible();
    expect(await anchors.count()).toBeGreaterThanOrEqual(2);
    await anchors.first().click();
    await expect(page).toHaveURL(/#family-/);
  });
});

test.describe('experiment comparison', () => {
  test('best-first headline names a winner and a start-here run', async ({ page }) => {
    await openExperiments(page);
    const headline = page.getByText('Best delivery').first();
    await expect(headline).toContainText('delivered to spec');
    await expect(page.getByText(/trails by/).first()).toBeVisible();
  });

  test('row expands to where-points-lost / what-held-up and run pills', async ({ page }) => {
    await openExperiments(page);
    const row = page.locator('tbody tr').first();
    await row.click();
    await expect(page.getByText('Where points were lost').first()).toBeVisible();
    await expect(page.getByText(/What held up/).first()).toBeVisible();
    const pill = page.locator('a[href^="/runs/"]').first();
    await expect(pill).toBeVisible();
    await row.click();
    await expect(page.getByText('Where points were lost')).toHaveCount(0);
  });

  test('Δ-vs-best and unscored confidence indicator render', async ({ page }) => {
    await openExperiments(page);
    await expect(page.getByText(/vs best/).first()).toBeVisible();
    await expect(page.getByText(/\d+ unscored/).first()).toBeVisible();
  });

  test('run pill opens the run detail', async ({ page }) => {
    await openExperiments(page);
    await page.locator('tbody tr').first().click();
    await page.locator('a[href^="/runs/"]').first().click();
    await expect(page).toHaveURL(/\/runs\/.+/);
    await expect(page.getByText('Why it scored this')).toBeVisible();
  });
});

test.describe('run detail — failing run', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`/runs/${FAILING_RUN}`);
    await expect(page.getByText('Why it scored this')).toBeVisible();
  });

  test('verdict banner, gates and compare-specs link', async ({ page }) => {
    await expect(page.getByText('Failing').first()).toBeVisible();
    await expect(page.getByText(/Did not deliver/)).toBeVisible();
    const gate = page.locator('button[title*="evidence"]').first();
    await expect(gate).toBeVisible();
    await gate.click();
    await expect(page).toHaveURL(/span=/);
    await page.getByRole('link', { name: /Compare agent specs/ }).click();
    await expect(page).toHaveURL(/#family-/);
  });

  test('technical details disclosure reveals the run id', async ({ page }) => {
    await page.getByText('Technical details').click();
    await expect(page.getByText('run id')).toBeVisible();
    await expect(page.getByText(FAILING_RUN).first()).toBeVisible();
  });

  test('scorecard checks are evidence-linked and clickable', async ({ page }) => {
    const checks = page.locator('button[title*="of this area"]');
    expect(await checks.count()).toBeGreaterThan(0);
    // click the first enabled check
    const count = await checks.count();
    for (let i = 0; i < count; i++) {
      if (await checks.nth(i).isEnabled()) {
        await checks.nth(i).click();
        break;
      }
    }
    await expect(page).toHaveURL(/span=/);
  });

  test('findings jump to their evidence span', async ({ page }) => {
    const jump = page.getByRole('button', { name: /jump:/ }).first();
    await expect(jump).toBeVisible();
    await jump.click();
    await expect(page).toHaveURL(/span=/);
  });
});

test.describe('run detail — unscored run', () => {
  test('shows the unscored verdict and reason banner', async ({ page }) => {
    await page.goto(`/runs/${UNSCORED_RUN}`);
    await expect(page.getByText('This run was not scored')).toBeVisible();
    await expect(page.getByText('Unscored').first()).toBeVisible();
    await expect(page.getByText('Why this run is unscored')).toBeVisible();
    await expect(page.getByText(/harness exited before verification/)).toBeVisible();
  });
});

test.describe('run detail — passing run', () => {
  test('shows a strong verdict, green scorecard checks and good findings', async ({ page }) => {
    await page.goto(`/runs/${PASSING_RUN}`);
    await expect(page.getByText('Delivered to spec')).toBeVisible();
    await expect(page.getByText('Strong').first()).toBeVisible();
    expect(await page.locator('button[title*="of this area"]').count()).toBeGreaterThan(0);
  });
});

test.describe('per-run evidence search', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`/runs/${FAILING_RUN}`);
    await expect(page.getByText('Why it scored this')).toBeVisible();
  });

  test('plain search returns matches and a result selects its span', async ({ page }) => {
    await page.getByPlaceholder(/Search this run/).fill('ledger');
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    await expect(page.getByText(/\d+ match/)).toBeVisible();
    await page.locator('button:has(span:has-text("ledger"))').last().click();
    await expect(page).toHaveURL(/span=/);
  });

  test('regex search returns matches', async ({ page }) => {
    await page.getByPlaceholder(/Search this run/).fill('bun run (test|lint)');
    await page.getByLabel('regex').check();
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    await expect(page.getByText(/\d+ match/)).toBeVisible();
  });
});

test.describe('span tree', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`/runs/${FAILING_RUN}`);
    await expect(page.locator('[role="tree"]')).toBeVisible();
  });

  test('error cycle selects an error span', async ({ page }) => {
    await page.locator('button[title="Cycle through error spans"]').click();
    await expect(page).toHaveURL(/span=/);
  });

  test('collapse-all and expand-all change row counts', async ({ page }) => {
    const rows = page.locator('[data-span-row]');
    const before = await rows.count();
    await page.locator('button[title="Collapse to sections"]').click();
    await expect(async () => expect(await rows.count()).toBeLessThan(before)).toPass();
    await page.locator('button[title="Expand all"]').click();
    await expect(async () => expect(await rows.count()).toBeGreaterThanOrEqual(before)).toPass();
  });

  test('row click opens detail; keyboard navigates; escape clears', async ({ page }) => {
    await page.locator('[data-span-row]').first().click();
    await expect(page.getByRole('button', { name: 'Annotate this span' })).toBeVisible();
    await page.locator('[role="tree"]').focus();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowDown');
    await expect(page).toHaveURL(/span=/);
    await page.keyboard.press('Escape');
    await expect(page).not.toHaveURL(/span=/);
  });

  test('payload copy button is present on a selected span', async ({ page }) => {
    await page.locator('[data-span-row]').first().click();
    await expect(page.locator('button[title="Copy payload"]').first()).toBeVisible();
  });
});

test.describe('annotations', () => {
  // The annotation create form: the innermost div wrapping the textarea also
  // holds the issue/good/note kind toggles, so scope kind clicks to it.
  const formOf = (page: Page) =>
    page.locator('div', { has: page.getByPlaceholder('What did you notice?') }).last();

  test('create then delete a run annotation', async ({ page }) => {
    await page.goto(`/runs/${PASSING_RUN}`);
    const form = formOf(page);
    await expect(page.getByPlaceholder('What did you notice?')).toBeVisible();
    const note = `E2E-${Date.now()}`;
    await form.getByRole('button', { name: 'note' }).click();
    await page.getByPlaceholder('What did you notice?').fill(note);
    await page.getByRole('button', { name: /Annotate run/ }).click();
    await expect(page.getByText(note)).toBeVisible();
    await page.locator('button[title="Delete annotation"]').last().click();
    await expect(page.getByText(note)).toHaveCount(0);
  });

  test('kind toggle switches the submit affordance', async ({ page }) => {
    await page.goto(`/runs/${PASSING_RUN}`);
    await formOf(page).getByRole('button', { name: 'issue' }).click();
    await expect(page.getByRole('button', { name: /Annotate run/ })).toBeVisible();
  });
});

test.describe('runs sidebar', () => {
  test('filter narrows the list and shows a no-match message', async ({ page }) => {
    await page.goto(`/runs/${FAILING_RUN}`);
    const items = page.locator('aside [data-run-id]');
    await expect(items.first()).toBeVisible();
    const before = await items.count();
    await page.locator('aside input').fill('skill');
    await expect(async () => expect(await items.count()).toBeLessThan(before)).toPass();
    await page.locator('aside input').fill('zzzznomatch');
    await expect(page.getByText('No runs match the filter')).toBeVisible();
  });

  test('selecting a run navigates to it', async ({ page }) => {
    await page.goto(`/runs/${FAILING_RUN}`);
    const item = page.locator('aside [data-run-id]').first();
    const id = await item.getAttribute('data-run-id');
    await item.click();
    await expect(page).toHaveURL(new RegExp(`/runs/${id!.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`));
  });

  test('empty state links back to Experiments', async ({ page }) => {
    await page.goto('/runs');
    await expect(page.getByText('Pick a run to see why')).toBeVisible();
    await page.locator('main').getByRole('link', { name: 'Experiments' }).click();
    await expect(page).toHaveURL(/\/$/);
  });
});

test.describe('comparison visualisations', () => {
  test('scatter point opens a run', async ({ page }) => {
    await openExperiments(page);
    const point = page.locator('svg circle').first();
    await expect(point).toBeVisible();
    await point.click();
    await expect(page).toHaveURL(/\/runs\/.+/);
  });

  test('failure patterns panel renders', async ({ page }) => {
    await openExperiments(page);
    await expect(page.getByText('Failure patterns').first()).toBeVisible();
  });

  test('revision movement diff expands with tabs', async ({ page }) => {
    await openExperiments(page);
    const card = page.getByRole('button', { name: /contract changes/ }).first();
    await card.scrollIntoViewIfNeeded();
    await card.click();
    await expect(page.getByRole('button', { name: /scenario \(/ })).toBeVisible();
  });
});

test('no console or page errors across the core surfaces', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(e.message));
  await openExperiments(page);
  await page.locator('tbody tr').first().click();
  await page.goto(`/runs/${FAILING_RUN}`);
  await expect(page.getByText('Why it scored this')).toBeVisible();
  await page.goto(`/runs/${UNSCORED_RUN}`);
  await expect(page.getByText('This run was not scored')).toBeVisible();
  expect(errors).toEqual([]);
});
