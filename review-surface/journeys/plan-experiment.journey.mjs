// Journey: orchestrator CLI. A researcher writes a new two-arm experiment, validates and locks
// it, confirms the lock is current, and dry-runs its trial plan without running an agent.
// Run: node scripts/surface/lever.mjs --data-dir <empty dir> --evidence-dir <dir> \
//        --journey review-surface/journeys/plan-experiment.journey.mjs
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { surface } from '../../scripts/surface/lever.mjs';

test('a new two-arm experiment validates, locks and plans every trial', async () => {
  const lever = surface();
  const experiment = path.join(lever.dataDir, 'cli', 'python__surface__journey', 'experiment.yaml');
  fs.mkdirSync(path.dirname(experiment), { recursive: true });
  const step = async (args) => {
    const result = await lever.raidar(args);
    assert.equal(result.code, 0, `raidar ${args.join(' ')} failed:\n${result.stderr}`);
    return result;
  };

  await step(['init', experiment]);
  await step(['validate', experiment]);
  await step(['lock', experiment]);
  const check = await step(['lock', '--check', experiment]);
  assert.match(check.stdout + check.stderr, /Lock is current/);
  const plan = JSON.parse((await step(['plan', '--json', experiment])).stdout);

  const arms = [...new Set(plan.trials.map((trial) => trial.arm))].sort();
  assert.deepEqual(arms, ['baseline', 'treatment']);
  assert.equal(plan.trials.length, 10);
  assert.ok(plan.trials.every((trial) => trial.selected));
  const trialHashes = plan.trials.map((trial) => `${trial.trial_id}:${trial.hashes.trial}`).sort();
  lever.record({
    arms,
    trials: plan.trials.length,
    lock_sha256: plan.lock_sha256,
    trial_hashes_sha256: createHash('sha256').update(trialHashes.join('\n')).digest('hex'),
  });
});
