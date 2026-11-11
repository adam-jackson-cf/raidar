// Journey: Trace. A harness debugger cycles to an error span, searches the trial's payloads,
// and leaves an annotation that persists in the instance's data directory until deleted.
// Run: node scripts/surface/lever.mjs --data-dir <empty dir> --evidence-dir <dir> \
//        --journey review-surface/journeys/trace-and-annotate.journey.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { surface } from '../../scripts/surface/lever.mjs';

// Deterministic ids from the synthetic review fixture.
const FAILING_RUN = 'synthetic-bugfix-gpt-5.5-low-04';
const NOTE = 'journey: ledger write fails before verification';

test('a debugger traces a failing trial to its evidence and annotates it', async () => {
  const lever = surface();
  const stored = path.join(lever.dataDir, 'surface', 'data', 'user-annotations.json');
  const storedNotes = () =>
    fs.existsSync(stored)
      ? JSON.parse(fs.readFileSync(stored, 'utf8')).map((item) => `${item.span_id}|${item.kind}|${item.note}`)
      : [];

  const outcome = await lever.withPage(async (page, { errors }) => {
    await page.goto(`/runs/${FAILING_RUN}`);
    await page.locator('[role="tree"]').waitFor();
    await page.locator('button[title="Cycle through error spans"]').click();
    await page.waitForURL(/span=/);
    const errorSpan = new URL(page.url()).searchParams.get('span');

    await page.getByPlaceholder(/Search this trial/).fill('ledger');
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    const matches = page.getByText(/\d+ match/);
    await matches.waitFor();
    const matchText = (await matches.textContent()).trim();
    await page.locator('button:has(span:has-text("ledger"))').last().click();
    await page.waitForURL((url) => url.searchParams.get('span') !== errorSpan);
    const evidenceSpan = new URL(page.url()).searchParams.get('span');

    const form = page.locator('div', { has: page.getByPlaceholder('What did you notice?') }).last();
    await form.getByRole('button', { name: 'issue' }).click();
    await page.getByPlaceholder('What did you notice?').fill(NOTE);
    await page.getByRole('button', { name: /Annotate span/ }).click();
    await page.getByText(NOTE).first().waitFor();
    assert.deepEqual(storedNotes(), [`${evidenceSpan}|issue|${NOTE}`]);

    await page.reload();
    await page.getByText(NOTE).first().waitFor();
    await page.locator('button[title="Delete annotation"]').last().click();
    await page.getByText(NOTE).first().waitFor({ state: 'detached' });
    assert.deepEqual(storedNotes(), []);
    assert.deepEqual(errors, []);
    return { error_span: errorSpan, search: matchText, annotated_span: evidenceSpan, annotation_persisted_and_deleted: true };
  });
  lever.record(outcome);
});
