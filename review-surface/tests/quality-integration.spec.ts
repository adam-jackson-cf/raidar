import { test, expect, type Page } from '@playwright/test';

// The Quality element inside the review flow (design memo §12.3): header navigation,
// run detail's Quality panel, comparison → Quality links and the headline-policy
// mismatch notice. Ids come from the experiment fixture (make review-fixture-synthetic),
// whose runs the suite also projects into Quality data (make review-surface-test).
const PASSING_RUN = 'synthetic-bugfix-gpt-5.5-medium-01';
const INELIGIBLE_RUN = 'synthetic-bugfix-gpt-5.5-low-05';
const POLICY_MIX_RUN = 'synthetic-policy-mix-gpt-5.5-low-01';
// Projected for review from the fixture's review-only root, which has no Quality data.
const REVIEW_ONLY_RUN = 'synthetic-review-only-gpt-5.5-low-01';
const QUALITY_ID = (trial: string) => `synthetic-bugfix.synthetic-bugfix.${trial}`;
const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

async function openRun(page: Page, runId: string) {
  await page.goto(`/runs/${runId}`);
  await expect(page.getByText('Why it scored this')).toBeVisible();
}

test.describe('quality navigation entry', () => {
  test('the header links to Quality and marks it active on its pages', async ({ page }) => {
    await page.goto('/');
    const nav = page.locator('header nav');
    await nav.getByRole('link', { name: 'Quality', exact: true }).click();
    await expect(page).toHaveURL(/\/quality$/);
    await expect(page.getByTestId('quality-page')).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Quality', exact: true })).toHaveClass(/active/);
  });
});

test.describe('run detail quality panel', () => {
  test('sits under the scorecard with the checkpoint strip and headline S', async ({ page }) => {
    await openRun(page, PASSING_RUN);
    const panel = page.getByTestId('run-quality-panel');
    await expect(panel).toBeVisible();
    const scorecardBox = await page.getByText('Why it scored this').boundingBox();
    const panelBox = await panel.boundingBox();
    expect(panelBox!.y).toBeGreaterThan(scorecardBox!.y);
    await expect(panel.getByTestId('strip-row')).toHaveCount(3);
    await expect(panel.getByTestId('strip-problem')).toContainText('scb-canonical@1');
    await expect(panel.getByTestId('run-quality-score')).toContainText(/S \d+\.\d/);
    // The first checkpoint has no transition into it: R and G are not applicable, with the reason.
    const notApplicable = panel.getByTestId('strip-row').first().locator('[data-status="not_applicable"]').first();
    await expect(notApplicable).toHaveAttribute('title', /.+/);
  });

  test('eligibility badges carry the scorer reason, never a zero', async ({ page }) => {
    // Unscored run: no scorecard, but its Quality panel still explains every cell.
    await page.goto(`/runs/${INELIGIBLE_RUN}`);
    const panel = page.getByTestId('run-quality-panel');
    await expect(panel.getByTestId('quality-strip')).toBeVisible();
    const headline = panel.getByTestId('run-quality-score').locator('[data-status="ineligible"]');
    await expect(headline).toHaveAttribute('title', /component_ineligible\[C\]/);
    await expect(panel.getByTestId('strip-row').nth(1).locator('[data-status="unavailable"]').first()).toHaveAttribute(
      'title',
      /no stored snapshot to measure/,
    );
    await expect(panel.getByTestId('policy-reasons')).toContainText('Headline S not eligible');
  });

  test('a cell drills down into the full quality run view at that checkpoint', async ({ page }) => {
    await openRun(page, PASSING_RUN);
    const panel = page.getByTestId('run-quality-panel');
    await panel.getByRole('button', { name: /Erosion: .*Show evidence/ }).nth(1).click();
    await expect(page).toHaveURL(
      new RegExp(`/quality/runs/${escape(QUALITY_ID('gpt-5.5-medium-01'))}\\?component=E&checkpoint=checkpoint_2$`),
    );
    const drilldown = page.getByTestId('evidence-drilldown');
    await expect(drilldown).toContainText('checkpoint_2');
    await drilldown.getByRole('button', { name: 'Close evidence' }).click();
    await expect(page).toHaveURL(new RegExp(`/quality/runs/${escape(QUALITY_ID('gpt-5.5-medium-01'))}$`));
  });

  test('a column drills down across all checkpoints', async ({ page }) => {
    await openRun(page, PASSING_RUN);
    await page.getByTestId('run-quality-panel').getByRole('button', { name: 'Rework evidence, all checkpoints' }).click();
    await expect(page).toHaveURL(/\?component=R$/);
    await expect(page.getByTestId('evidence-drilldown')).toContainText('all checkpoints');
  });

  test('links open the full quality run view and the arm comparison', async ({ page }) => {
    await openRun(page, PASSING_RUN);
    const panel = page.getByTestId('run-quality-panel');
    await panel.getByRole('link', { name: 'Full quality view' }).click();
    await expect(page).toHaveURL(new RegExp(`/quality/runs/${escape(QUALITY_ID('gpt-5.5-medium-01'))}$`));
    await expect(page.getByTestId('quality-strip')).toBeVisible();
    await openRun(page, PASSING_RUN);
    await page.getByTestId('run-quality-panel').getByRole('link', { name: 'Compare arms' }).click();
    await expect(page).toHaveURL(/\/quality\?experiment=synthetic-bugfix&lock=[0-9a-f]{12}&problem=synthetic%2Fledger-balance%401$/);
    await expect(page.locator('[data-testid="quality-problem"][data-focused]')).toHaveAttribute(
      'aria-label',
      'synthetic/ledger-balance@1',
    );
  });

  test('collapses to its header', async ({ page }) => {
    await openRun(page, PASSING_RUN);
    const panel = page.getByTestId('run-quality-panel');
    await panel.getByRole('button', { name: 'Quality', exact: true }).click();
    await expect(panel.getByTestId('quality-strip')).toHaveCount(0);
    await expect(panel.getByTestId('run-quality-score')).toBeVisible();
  });

  test('runs without Quality data say so instead of showing a strip', async ({ page }) => {
    await openRun(page, REVIEW_ONLY_RUN);
    const panel = page.getByTestId('run-quality-panel');
    await expect(panel).toContainText('Not assessed yet');
    await expect(panel.getByTestId('quality-strip')).toHaveCount(0);
    await expect(panel.getByTestId('run-quality-score')).toHaveCount(0);
  });
});

test.describe('comparison views link to the experiment quality view', () => {
  test('a comparison group opens its experiment quality view at its problem', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('Best delivery').first()).toBeVisible();
    const family = page.locator('[id="family-synthetic/ledger-balance@1"]');
    await family.getByTestId('group-quality-link').getByRole('link', { name: 'Quality' }).click();
    await expect(page).toHaveURL(/\/quality\?experiment=synthetic-bugfix&lock=[0-9a-f]{12}&problem=synthetic%2Fledger-balance%401$/);
    await expect(page.getByRole('combobox', { name: 'Experiment' })).toHaveValue(/^synthetic-bugfix@/);
    await expect(page.getByRole('heading', { name: 'synthetic/ledger-balance@1' })).toBeVisible();
  });

  test('an expanded arm links to the same view without toggling the row', async ({ page }) => {
    await page.goto('/');
    const family = page.locator('[id="family-synthetic/ledger-balance@1"]');
    await family.locator('tbody tr').first().click();
    const link = family.getByTestId('arm-quality-link').getByRole('link', { name: 'Quality' });
    await expect(link).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(/\/quality\?experiment=synthetic-bugfix&/);
  });

  test('families without Quality data have no quality link', async ({ page }) => {
    await page.goto('/');
    const family = page.locator('[id="family-synthetic/review-only@1"]');
    await expect(family).toBeVisible();
    await expect(family.getByTestId('group-quality-link')).toHaveCount(0);
  });
});

test.describe('headline-policy mismatch notice', () => {
  const FAMILY = 'synthetic/policy-mix@1';

  test('shows on the compared family without expanding a row, naming each policy', async ({ page }) => {
    await page.goto('/');
    const notice = page.locator(`[id="family-${FAMILY}"]`).getByTestId('policy-mismatch');
    await expect(notice).toBeVisible();
    await expect(notice).toContainText('Headline metrics differ in this comparison');
    await expect(notice).toContainText('scb-canonical-oneshot@1');
    await expect(notice).toContainText(/scb-canonical@1/);
    await expect(page.locator('[id="family-synthetic/ledger-balance@1"]').getByTestId('policy-mismatch')).toHaveCount(0);
  });

  test('shows the same notice in run detail and on both quality views', async ({ page }) => {
    await openRun(page, POLICY_MIX_RUN);
    await expect(page.getByTestId('policy-mismatch')).toContainText('Headline metrics differ in this comparison');
    await page.getByTestId('run-quality-panel').getByRole('link', { name: 'Compare arms' }).click();
    const problem = page.locator(`[data-testid="quality-problem"][aria-label="${FAMILY}"]`);
    await expect(problem.getByTestId('policy-mismatch')).toContainText('scb-canonical-oneshot@1');
    await page.goto(`/quality/runs/synthetic-policy-mix.synthetic-policy-mix.gpt-5.5-low-01`);
    await expect(page.getByTestId('policy-mismatch')).toContainText('Headline metrics differ in this comparison');
  });

  test('a single-policy family shows no notice anywhere', async ({ page }) => {
    await openRun(page, PASSING_RUN);
    await expect(page.getByTestId('run-quality-panel')).toBeVisible();
    await expect(page.getByTestId('policy-mismatch')).toHaveCount(0);
  });
});

test('no console or page errors across the quality entry points', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(e.message));
  await openRun(page, PASSING_RUN);
  await expect(page.getByTestId('run-quality-panel').getByTestId('quality-strip')).toBeVisible();
  await openRun(page, REVIEW_ONLY_RUN);
  await page.goto('/quality?experiment=synthetic-bugfix&problem=synthetic%2Fledger-balance%401');
  await expect(page.getByTestId('quality-problem').first()).toBeVisible();
  await page.goto(`/quality/runs/${QUALITY_ID('gpt-5.5-low-05')}?component=V&checkpoint=checkpoint_2`);
  await expect(page.getByTestId('evidence-drilldown')).toBeVisible();
  expect(errors).toEqual([]);
});

test('run detail Quality panel shows a gated trial\'s gate rounds and failure families', async ({ page }) => {
  // The experiment fixture has no gated arm, so this run's Quality detail carries the gate
  // view of the Quality fixture's gated trial (same contract, same component).
  const gated = await (await page.request.get('/api/quality/runs/synthetic-quality.synthetic-run-1.b00-p0-guided')).json();
  expect(gated.gate).toBeTruthy();
  await page.route(`**/api/quality/runs/${encodeURIComponent(QUALITY_ID('gpt-5.5-medium-01'))}`, async (route) => {
    const detail = await (await route.fetch()).json();
    await route.fulfill({ json: { ...detail, gate: gated.gate } });
  });
  await openRun(page, PASSING_RUN);
  const panel = page.getByTestId('run-quality-panel');
  const gate = panel.getByTestId('gate-panel');
  await expect(gate).toBeVisible();
  await expect(gate.getByTestId('gate-step')).toHaveCount(5);
  await gate.getByRole('button', { name: 'checkpoint_4 gate rounds' }).click();
  await expect(gate.getByTestId('gate-round')).toHaveCount(4);
});

test('run detail Quality panel of an ungated trial has no gate section', async ({ page }) => {
  await openRun(page, PASSING_RUN);
  await expect(page.getByTestId('run-quality-panel').getByTestId('quality-strip')).toBeVisible();
  await expect(page.getByTestId('gate-panel')).toHaveCount(0);
});
