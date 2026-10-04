import { test, expect, type Page } from '@playwright/test';

// Deterministic ids from the synthetic Quality fixture (make review-surface-quality-data).
const INELIGIBLE_RUN = 'synthetic-quality.synthetic-run-1.b02-p0-baseline';
const LABELLED_RUN = 'synthetic-quality.synthetic-run-1.b00-p0-baseline';
// The fixture's guided arm runs behind a commit gate (gate-effort@1); baseline does not.
const GATED_RUN = 'synthetic-quality.synthetic-run-1.b00-p0-guided';

// The suite's Quality data also covers the experiment fixture's runs (run detail's Quality
// panel), so the labelled Quality experiment is opened by its deep link.
async function openQuality(page: Page) {
  await page.goto('/quality?experiment=synthetic-quality');
  await expect(page.getByTestId('quality-problem').first()).toBeVisible();
}

async function removeLabels(page: Page, runId: string) {
  const labels = await (await page.request.get(`/api/quality/labels?run_id=${runId}`)).json();
  for (const label of labels) await page.request.delete(`/api/quality/labels/${encodeURIComponent(label.id)}`);
}

test.describe('quality navigation', () => {
  test('the /quality route opens the experiment view under its lock, labelled synthetic', async ({ page }) => {
    await page.goto('/quality');
    await expect(page.getByTestId('quality-problem').first()).toBeVisible();
    await expect(page.getByText('Synthetic', { exact: true })).toBeVisible();
    const selector = page.getByRole('combobox', { name: 'Experiment' });
    await expect(selector).toContainText(/synthetic-quality · lock [0-9a-f]{12}/);
    const option = selector.locator('option', { hasText: 'synthetic-quality' });
    await selector.selectOption((await option.getAttribute('value'))!);
    await expect(page).toHaveURL(/experiment=synthetic-quality&lock=[0-9a-f]{12}/);
    await expect(page.getByRole('heading', { name: 'synthetic/ledger@0' })).toBeVisible();
  });

  test('the experiment view names its pre-registered primary outcome', async ({ page }) => {
    await openQuality(page);
    await expect(page.getByTestId('primary-outcome')).toContainText('Primary outcome: solve_rate');
    await expect(page.getByTestId('primary-outcome')).toContainText('default for continue progression');
    await expect(page.getByTestId('primary-raw')).toHaveCount(0);
  });

  test('an errata-applied primary names the raw outcome reported beside it', async ({ page }) => {
    await page.route('**/api/quality/experiments', async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      for (const item of body.experiments) {
        item.primary_outcome = 'solve_rate_errata';
        item.primary_source = 'lock (analysis.primary_outcome)';
        item.primary_raw = 'solve_rate';
      }
      await route.fulfill({ response, json: body });
    });
    await openQuality(page);
    await expect(page.getByTestId('primary-outcome')).toContainText('Primary outcome: solve_rate_errata');
    await expect(page.getByTestId('primary-outcome')).toContainText('lock (analysis.primary_outcome)');
    await expect(page.getByTestId('primary-raw')).toContainText('raw solve_rate reported beside it');
  });

  test('a trial link opens its run detail and back returns', async ({ page }) => {
    await openQuality(page);
    await page.getByTestId('quality-trials').getByRole('link', { name: 'b02-p0-baseline' }).click();
    await expect(page).toHaveURL(new RegExp(`/quality/runs/${INELIGIBLE_RUN.replace(/\./g, '\\.')}$`));
    await expect(page.getByTestId('quality-strip')).toBeVisible();
    // Back returns to the run's own experiment and problem (the header nav has its own Quality link).
    await page.getByTestId('quality-run-page').getByRole('link', { name: 'Quality', exact: true }).click();
    await expect(page).toHaveURL(/\/quality\?experiment=synthetic-quality&lock=[0-9a-f]{12}&problem=synthetic%2Fledger%400$/);
    await expect(page.getByRole('heading', { name: 'synthetic/ledger@0' })).toBeVisible();
  });
});

test.describe('quality experiment view', () => {
  test('component curves: one small multiple per component, both arms in the legend', async ({ page }) => {
    await openQuality(page);
    await expect(page.getByTestId('component-curve')).toHaveCount(8);
    const legend = page.getByRole('list', { name: 'Arms' });
    await expect(legend).toContainText('baseline');
    await expect(legend).toContainText('guided');
  });

  test('paired Δ gives a verdict per component and accounts for left-out blocks', async ({ page }) => {
    await openQuality(page);
    const table = page.getByTestId('paired-delta');
    await expect(table.getByTestId('paired-row')).toHaveCount(8);
    await expect(table.getByText('Improved').first()).toBeVisible();
    await expect(table.getByText('Insufficient').first()).toBeVisible();
    await expect(page.getByTestId('quality-exclusions')).toContainText('invalid trial (infra-excluded)');
  });

  test('SCB distributions, head-to-head scatter and sensitivity render with their sources', async ({ page }) => {
    await openQuality(page);
    await expect(page.getByTestId('distribution')).toHaveCount(3);
    await expect(page.getByTestId('quality-distributions')).toContainText('Lint density');
    await expect(page.getByTestId('quality-distributions')).toContainText('CC-mass concentration');
    await expect(page.getByTestId('quality-distributions')).toContainText('Function LOC');
    const scatters = page.getByTestId('h2h-scatter');
    await expect(scatters).toHaveCount(2);
    await expect(scatters.first()).toContainText('Lint errors per checkpoint');
    await expect(scatters.nth(1)).toContainText('Functions with CC > 10');
    await expect(page.getByTestId('h2h-counts').first()).toHaveText(/\d+ plotted · \d+ excluded/);
    await expect(page.getByTestId('ablation-row')).toHaveCount(5);
    await expect(page.getByTestId('correlation-matrix')).toContainText(/-?\d\.\d\d/);
  });
});

test.describe('quality run detail', () => {
  test('strip shows every checkpoint plus the headline row, with eligibility reasons', async ({ page }) => {
    await page.goto(`/quality/runs/${INELIGIBLE_RUN}`);
    await expect(page.getByTestId('strip-row')).toHaveCount(5);
    await expect(page.getByTestId('strip-problem')).toContainText('scb-canonical@1');
    const badge = page.getByTestId('strip-problem').locator('[data-status="ineligible"]').first();
    await expect(badge).toHaveAttribute('title', /no value for checkpoint_3/);
    await expect(page.getByTestId('policy-reasons')).toContainText('component_ineligible H');
    await expect(page.getByTestId('strip-row').first().locator('[data-status="not_applicable"]').first()).toHaveAttribute(
      'title',
      /no transition into the first checkpoint/,
    );
  });

  test('a cell drill-down lists file:line evidence for that checkpoint', async ({ page }) => {
    await page.goto(`/quality/runs/${INELIGIBLE_RUN}`);
    await page.getByRole('button', { name: /Erosion: .*Show evidence/ }).nth(3).click();
    const drilldown = page.getByTestId('evidence-drilldown');
    await expect(drilldown).toContainText('checkpoint_4');
    await expect(drilldown.getByTestId('evidence-item').first()).toContainText(/ledger\/\w+\.py:\d+-\d+/);
    await drilldown.getByRole('button', { name: 'Close evidence' }).click();
    await expect(page.getByTestId('evidence-drilldown')).toHaveCount(0);
  });

  test('a column header drill-down spans all checkpoints', async ({ page }) => {
    await page.goto(`/quality/runs/${INELIGIBLE_RUN}`);
    await page.getByRole('button', { name: 'Rework evidence, all checkpoints' }).click();
    const drilldown = page.getByTestId('evidence-drilldown');
    await expect(drilldown).toContainText('all checkpoints');
    await expect(drilldown.getByTestId('evidence-item').first()).toContainText('->');
  });

  test('an unknown run says so and offers the way back', async ({ page }) => {
    await page.goto('/quality/runs/no-such-run');
    await expect(page.getByText('No quality data for no-such-run.')).toBeVisible();
  });
});

test.describe('parked jev decisions', () => {
  test.afterEach(async ({ page }) => removeLabels(page, LABELLED_RUN));

  test('decisions are collapsed, read-only history with no labelling controls', async ({ page }) => {
    await page.goto(`/quality/runs/${LABELLED_RUN}`);
    const history = page.getByTestId('jev-history');
    await expect(history).toBeVisible();
    await expect(history).toContainText('parked, read-only history; not part of any score');
    const decision = history.getByTestId('jev-history-decision').first();
    await expect(decision).toBeHidden();
    await history.locator('summary').click();
    await expect(decision).toBeVisible();
    await expect(decision).toContainText('code-quality.rubric-smells');
    await expect(history.getByRole('button')).toHaveCount(0);
    await expect(page.getByTestId('jev-decision')).toHaveCount(0);
  });

  test('the label API refuses unknown decisions and labels outside the vocabulary', async ({ page }) => {
    const bad = await page.request.post('/api/quality/labels', {
      data: { run_id: LABELLED_RUN, request_id: 'jevp_missing', policy: 'code-quality.rubric-smells', label: 'correct' },
    });
    expect(bad.status()).toBe(404);
    const vocabulary = await page.request.post('/api/quality/labels', {
      data: { run_id: LABELLED_RUN, request_id: 'x', policy: 'code-quality.rubric-smells', label: 'ok' },
    });
    expect(vocabulary.status()).toBe(400);
    const unnamedModel = await page.request.post('/api/quality/labels', {
      data: { run_id: LABELLED_RUN, request_id: 'x', policy: 'code-quality.rubric-smells', label: 'correct', reviewer: 'model' },
    });
    expect(unnamedModel.status()).toBe(400);
  });
});

test.describe('quality run detail: commit gate', () => {
  test('a gated run shows its gate effort per checkpoint and each step expands to its rounds', async ({ page }) => {
    await page.goto(`/quality/runs/${GATED_RUN}`);
    const panel = page.getByTestId('gate-panel');
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId('gate-totals')).toContainText('First-pass clean');
    await expect(panel.getByTestId('gate-totals')).toContainText('Unresolved');
    await expect(panel.getByTestId('gate-step')).toHaveCount(5);
    const unresolved = panel.getByTestId('gate-step').nth(3);
    await expect(unresolved.getByTestId('gate-ending')).toHaveText('unresolved');
    await expect(panel.getByRole('columnheader', { name: 'typing' })).toBeVisible();
    await unresolved.getByRole('button', { name: 'checkpoint_4 gate rounds' }).click();
    const rounds = panel.getByTestId('gate-round');
    await expect(rounds).toHaveCount(4);
    await expect(rounds.first()).toHaveAttribute('data-verdict', 'refused');
    await expect(rounds.first()).toContainText(/\d+ blocking/);
    await expect(rounds.first()).toContainText('gate_run_1.log');
  });

  test('a shadow gate is labelled as never enforced and shows what it would have decided', async ({ page }) => {
    // gate-effort@2 shadow records (guardrail-gate@2 mode shadow): the run detail is the
    // fixture's gated run with its view marked shadow, as the projection writes it.
    await page.route(`**/api/quality/runs/${GATED_RUN}`, async (route) => {
      const response = await route.fetch();
      const detail = await response.json();
      detail.gate.shadow = true;
      detail.gate.scorer = 'gate-effort@2';
      for (const step of detail.gate.steps) step.termination = 'shadow_recorded';
      await route.fulfill({ response, json: detail });
    });
    await page.goto(`/quality/runs/${GATED_RUN}`);
    const panel = page.getByTestId('gate-panel');
    await expect(panel.getByTestId('gate-shadow')).toHaveText('shadow gate · never enforced');
    await expect(panel).toContainText('gate-effort@2');
    await expect(panel.getByTestId('gate-ending').first()).toHaveText(/^shadow: would (pass|refuse)$/);
  });

  test('an ungated run has no gate panel, not a row of zeros', async ({ page }) => {
    await page.goto(`/quality/runs/${LABELLED_RUN}`);
    await expect(page.getByTestId('quality-strip')).toBeVisible();
    await expect(page.getByTestId('gate-panel')).toHaveCount(0);
  });
});
