import { chromium } from '@playwright/test';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
const errors = [];
page.on('pageerror', (error) => errors.push(`PAGEERROR: ${error.message}`));
page.on('console', (msg) => {
  if (msg.type() === 'error') errors.push(`CONSOLE: ${msg.text()}`);
});

await page.goto('http://127.0.0.1:5950/wireframe', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('section[id^="family-"]', { timeout: 10000 });

const visibleRevisionBlocksCount = await page.locator('section[id^="family-"] .overflow-hidden.rounded-md.border').count();
const scenarioSections = page.locator('section[id^="family-"]');
const sectionsToCheck = Math.min(3, await scenarioSections.count());

const expectedVisiblePlotCount = async (sectionId) => {
  return page.evaluate(async (id) => {
    const section = document.querySelector(`[id=${JSON.stringify(id)}]`);
    if (!section) return { chartRuns: 0, visibleRevisions: [] };

    const family = section.id.replace('family-', '');
    const revisionBlocks = Array.from(section.querySelectorAll('.overflow-hidden.rounded-md.border'));
    const visibleRevisions = revisionBlocks
      .map((block) => {
        const headerText = block.querySelector('div')?.textContent ?? '';
        const match = headerText.match(/Revision\s+([\w.-]+)/);
        return match?.[1]?.trim();
      })
      .filter(Boolean);

    const [experimentsResponse, runsResponse] = await Promise.all([fetch('/api/experiments'), fetch('/api/runs')]);
    const experimentsPayload = await experimentsResponse.json();
    const runPayload = await runsResponse.json();
    const experiments = experimentsPayload.experiments ?? [];
    const runs = runPayload ?? [];

    const selectedRunIds = new Set(
      experiments
        .filter((exp) => visibleRevisions.includes(exp.revision) && exp.scenario === family)
        .flatMap((exp) => exp.run_ids),
    );

    const chartRuns = runs
      .filter((run) => selectedRunIds.has(run.id) && run.composite_score != null && run.duration_ms > 0)
      .reduce((buckets, run) => {
        const key = `${run.revision}|${run.agent_spec}`;
        const existing = buckets.get(key) ?? [];
        existing.push(run);
        buckets.set(key, existing);
        return buckets;
      }, new Map())
      .values();

    let bestRunCount = 0;
    for (const entries of chartRuns) {
      const sorted = [...entries].sort((left, right) => {
        const leftOutcome = left.composite_score ?? -1;
        const rightOutcome = right.composite_score ?? -1;
        if (leftOutcome !== rightOutcome) return rightOutcome - leftOutcome;

        const leftCost = (left.total_input_tokens ?? 0) + (left.total_output_tokens ?? 0);
        const rightCost = (right.total_input_tokens ?? 0) + (right.total_output_tokens ?? 0);
        if (leftCost !== rightCost) return leftCost - rightCost;

        if (left.duration_ms !== right.duration_ms) return left.duration_ms - right.duration_ms;

        const leftLabel = String(left.id).match(/(\d+)$/)?.[1];
        const rightLabel = String(right.id).match(/(\d+)$/)?.[1];
        const leftRun = leftLabel ? Number(leftLabel) : Number.MAX_SAFE_INTEGER;
        const rightRun = rightLabel ? Number(rightLabel) : Number.MAX_SAFE_INTEGER;
        return leftRun - rightRun;
      });
      if (sorted.length > 0) {
        bestRunCount += 1;
      }
    }

    return { chartRuns: bestRunCount, visibleRevisions };
  }, sectionId);
};

for (let i = 0; i < sectionsToCheck; i += 1) {
  const section = scenarioSections.nth(i);
  const sectionId = await section.getAttribute('id');
  if (!sectionId) continue;

  const initial = await expectedVisiblePlotCount(sectionId);
  const chart = section.locator('svg[aria-label="Outcome against run duration"]');
  const initialRender = await chart.count();

  if (initialRender === 0) {
    console.log(sectionId, 'chart missing (likely fewer than two valid runs)');
    continue;
  }

  const initialCircles = await chart.locator('circle').count();
  const afterMismatch = initialCircles !== initial.chartRuns;
  if (afterMismatch) {
    throw new Error(`Initial chart mismatch for ${sectionId}: rendered ${initialCircles} vs expected ${initial.chartRuns}`);
  }

  const visibleRevisionSections = section.locator('.overflow-hidden.rounded-md.border');
  const visibleRevisionCount = await visibleRevisionSections.count();

  if (visibleRevisionCount > 1) {
    const secondRevisionEye = visibleRevisionSections.nth(1).locator('button').first();
    await secondRevisionEye.click();
    await page.waitForTimeout(250);

    const afterHide = await expectedVisiblePlotCount(sectionId);
    const hiddenCircles = await chart.locator('circle').count();
    if (hiddenCircles !== afterHide.chartRuns) {
      throw new Error(`After hiding revision for ${sectionId}: rendered ${hiddenCircles} vs expected ${afterHide.chartRuns}`);
    }

    const familyReveal = section.locator('button[aria-label="Show all revision tables"]');
    const showAllVisible = await familyReveal.count();
    if (showAllVisible === 1) {
      await familyReveal.click();
      await page.waitForTimeout(250);
    }

    const afterShow = await expectedVisiblePlotCount(sectionId);
    const restoredCircles = await chart.locator('circle').count();
    if (restoredCircles !== afterShow.chartRuns) {
      throw new Error(`After restoring revisions for ${sectionId}: rendered ${restoredCircles} vs expected ${afterShow.chartRuns}`);
    }
  }
}

console.log('revisions', visibleRevisionBlocksCount);
console.log('errors', JSON.stringify(errors, null, 2));
await browser.close();
