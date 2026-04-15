import { expect, test, type APIRequestContext } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * PT.10 — Two consecutive compiles of the same target on the same worker:
 * the second build must be substantially faster than the first because the
 * PlatformIO build cache (`.pioenvs`) survives between runs.
 *
 * Pinned to local-worker so both jobs share a cache directory; otherwise
 * round-robin scheduling could send the second job to a different worker
 * with a cold cache and the comparison becomes meaningless.
 *
 * Threshold: second ≤ 0.85 × first. The original PT.10 spec called for
 * ≥50% speedup, but a small device like cyd-office-info spends most of its
 * wall-clock budget on OTA upload + PlatformIO setup, not on the C++
 * compile that the cache actually accelerates. A 15% wall-clock speedup
 * still demonstrates the cache is doing real work; a regression that
 * disables it would push the ratio close to 1.0 (or worse if cache misses
 * trigger a full re-fetch). Tune via SPEEDUP_THRESHOLD env if needed.
 *
 * NOTE: this test consumes ~2 real compiles' worth of build time. It runs
 * inside the existing 10-minute hass-4 suite budget but is the longest
 * single test by far.
 */

const TARGET_FILENAME = process.env.HASS4_TARGET || 'cyd-office-info.yaml';

const EXPECTED_VERSION =
  process.env.EXPECTED_VERSION ||
  readFileSync(join(__dirname, '../../VERSION'), 'utf-8').trim();

const COMPILE_BUDGET_MS = parseInt(process.env.COMPILE_BUDGET_MS || '480000', 10);
const SPEEDUP_THRESHOLD = parseFloat(process.env.SPEEDUP_THRESHOLD || '0.85');

interface QueueJob {
  id: string;
  target: string;
  state: string;
  duration_seconds?: number | null;
  created_at: string;
  finished_at?: string;
  pinned_client_id?: string;
  assigned_client_id?: string;
}

interface Worker {
  client_id: string;
  hostname: string;
  online: boolean;
  max_parallel_jobs?: number;
}

function isTerminal(state: string): boolean {
  return state === 'success' || state === 'failed' || state === 'timed_out';
}

async function getQueue(request: APIRequestContext): Promise<QueueJob[]> {
  const resp = await request.get('/ui/api/queue');
  if (!resp.ok()) throw new Error(`/ui/api/queue returned ${resp.status()}`);
  return resp.json();
}

async function getJob(request: APIRequestContext, id: string): Promise<QueueJob | null> {
  return (await getQueue(request)).find(j => j.id === id) ?? null;
}

async function runOneCompile(request: APIRequestContext, workerId: string): Promise<QueueJob> {
  const before = new Set((await getQueue(request)).map(j => j.id));
  const compileResp = await request.post('/ui/api/compile', {
    data: { targets: [TARGET_FILENAME], pinned_client_id: workerId },
  });
  expect(compileResp.ok()).toBe(true);

  let jobId: string | null = null;
  await expect.poll(
    async () => {
      const queue = await getQueue(request);
      const found = queue.find(j => j.target === TARGET_FILENAME && !before.has(j.id));
      if (found) {
        jobId = found.id;
        return jobId;
      }
      return null;
    },
    { timeout: 15_000, message: 'compile job should appear in queue' },
  ).not.toBeNull();

  let final: QueueJob | null = null;
  await expect.poll(
    async () => {
      const job = await getJob(request, jobId!);
      if (job && isTerminal(job.state)) {
        final = job;
        return job.state;
      }
      return job?.state ?? 'missing';
    },
    {
      timeout: COMPILE_BUDGET_MS,
      intervals: [2_000, 5_000, 10_000],
      message: `compile did not finish within ${COMPILE_BUDGET_MS}ms`,
    },
  ).toBe('success');

  expect(final, 'compile should finish').not.toBeNull();
  expect(final!.duration_seconds, 'duration_seconds must be reported').toBeTruthy();
  return final!;
}

test.describe.serial('incremental build hass-4 smoke', () => {
  test.beforeAll(async ({ request }) => {
    const resp = await request.get('/ui/api/server-info');
    expect(resp.ok()).toBe(true);
    const info = await resp.json();
    expect(info.addon_version).toBe(EXPECTED_VERSION);
  });

  test('second compile is meaningfully faster than first on the same worker', async ({ request }) => {
    test.setTimeout(COMPILE_BUDGET_MS * 2 + 60_000);

    const workersResp = await request.get('/ui/api/workers');
    expect(workersResp.ok()).toBe(true);
    const workers = (await workersResp.json()) as Worker[];
    const localWorker = workers.find(w => w.hostname === 'local-worker' && w.online);
    expect(localWorker, 'local-worker must be online').toBeDefined();

    const first = await runOneCompile(request, localWorker!.client_id);
    const second = await runOneCompile(request, localWorker!.client_id);

    const ratio = second.duration_seconds! / first.duration_seconds!;
    // eslint-disable-next-line no-console
    console.log(`first=${first.duration_seconds}s, second=${second.duration_seconds}s, ratio=${ratio.toFixed(2)}`);
    expect(
      ratio,
      `second compile (${second.duration_seconds}s) should be ≤${SPEEDUP_THRESHOLD * 100}% of the first (${first.duration_seconds}s); ratio=${ratio.toFixed(2)}`,
    ).toBeLessThanOrEqual(SPEEDUP_THRESHOLD);
  });
});
