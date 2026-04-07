import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * Production smoke test against a real distributed-esphome instance.
 *
 * Compiles a real device end-to-end:
 *   1. Devices tab loads with the target row
 *   2. Click Upgrade
 *   3. Job appears in the queue with state working
 *   4. Open the log modal and tail the streaming compile output
 *   5. Compile + OTA succeeds (state success)
 *   6. Open Live Logs from the device row and verify the device API stream
 *
 * The target device defaults to `cyd-office-info.yaml` and can be overridden
 * with the PROD_TARGET env var. The base URL is set in playwright.config.ts
 * via PROD_URL (default http://192.168.225.112:8765, the hass-4 instance).
 *
 * Run with:
 *   npm run test:e2e:prod
 */

const TARGET_FILENAME = process.env.PROD_TARGET || 'cyd-office-info.yaml';
const TARGET_STEM = TARGET_FILENAME.replace(/\.ya?ml$/, '');

// Read the expected add-on version from ha-addon/VERSION at test startup so
// the suite refuses to run against a stale deploy. Override with EXPECTED_VERSION
// if you intentionally want to test a different version.
const EXPECTED_VERSION =
  process.env.EXPECTED_VERSION ||
  readFileSync(join(__dirname, '../../VERSION'), 'utf-8').trim();

// Compile + OTA budget. Real ESP32 builds with PlatformIO can be slow on a
// cold cache; tune via env var if needed.
const COMPILE_BUDGET_MS = parseInt(process.env.COMPILE_BUDGET_MS || '480000', 10);

// How long we'll wait to see at least one log line stream into the modal.
const LOG_STREAM_TIMEOUT_MS = 60_000;

// How long we'll watch the device live log for an incoming line.
const DEVICE_LOG_TIMEOUT_MS = 30_000;

test.describe.serial('cyd-office-info production smoke', () => {
  // Confirm we're talking to the expected add-on version before doing anything
  // else. If the deploy is stale, the rest of the tests are meaningless.
  test.beforeAll(async ({ request }) => {
    const resp = await request.get('/ui/api/server-info');
    expect(resp.ok(), `server-info should return 2xx (got ${resp.status()})`).toBe(true);
    const info = await resp.json();
    expect(
      info.addon_version,
      `expected add-on version ${EXPECTED_VERSION}, got ${info.addon_version}. ` +
        `If you intentionally want to test a different version, set EXPECTED_VERSION.`,
    ).toBe(EXPECTED_VERSION);
  });

  test('devices tab loads and shows the target device', async ({ page }) => {
    await page.goto('/');

    // Header sanity — version badge should reflect the deployed version
    await expect(page.locator('header')).toBeVisible();
    await expect(page.getByText('Distributed Build')).toBeVisible();
    await expect(page.getByText(`v${EXPECTED_VERSION}`)).toBeVisible();

    // Devices tab is the default — wait for the device table to populate
    const targetRow = await findTargetRow(page);
    await expect(targetRow).toBeVisible({ timeout: 30_000 });
  });

  test('schedule upgrade and verify it lands in the queue', async ({ page }) => {
    await page.goto('/');
    const targetRow = await findTargetRow(page);
    await expect(targetRow).toBeVisible({ timeout: 30_000 });

    // Click the row's Upgrade button
    const upgradeBtn = targetRow.getByRole('button', { name: /^upgrade$/i });
    await expect(upgradeBtn).toBeVisible();
    await upgradeBtn.click();

    // Toast confirms the enqueue (or the page auto-switches to Queue)
    // Either way, switch to Queue tab and verify the job is there
    await page.getByRole('button', { name: /^Queue/ }).click();

    // The new job should appear within the polling interval (~3s)
    const queueRow = await findQueueRow(page);
    await expect(queueRow).toBeVisible({ timeout: 15_000 });
  });

  test('compile runs to completion and live log streams', async ({ page }) => {
    test.setTimeout(COMPILE_BUDGET_MS + 60_000);

    await page.goto('/');
    await page.getByRole('button', { name: /^Queue/ }).click();

    const queueRow = await findQueueRow(page);
    await expect(queueRow).toBeVisible({ timeout: 30_000 });

    // Open the log modal by clicking the row's Log button
    const logBtn = queueRow.getByRole('button', { name: /^log$/i });
    await expect(logBtn).toBeVisible({ timeout: 30_000 });
    await logBtn.click();

    // The log modal contains an xterm.js terminal — the screen renders text
    // into a div with class "xterm-screen". Wait for at least one log line.
    const terminal = page.locator('.xterm-screen').first();
    await expect(terminal).toBeVisible({ timeout: 10_000 });

    // Wait for the terminal to actually contain compile output
    await expect.poll(
      async () => (await terminal.textContent())?.length ?? 0,
      { timeout: LOG_STREAM_TIMEOUT_MS, message: 'expected log lines to stream' },
    ).toBeGreaterThan(50);

    // Close the log modal so we can watch the queue row state from outside
    await page.keyboard.press('Escape');

    // Poll the queue row's state badge until we see Success or Failed
    const stateBadge = queueRow.locator('span').filter({
      hasText: /^(Pending|Working|Compiling|Linking|Uploading|OTA|Success|Failed|OTA Failed|Timed Out)$/i,
    }).first();

    await expect.poll(
      async () => (await stateBadge.textContent())?.trim() ?? '',
      {
        timeout: COMPILE_BUDGET_MS,
        intervals: [2_000, 5_000, 10_000],
        message: `compile + OTA did not finish within ${COMPILE_BUDGET_MS}ms`,
      },
    ).toMatch(/^(Success|Failed|OTA Failed|Timed Out)$/i);

    // The test fails if we ended in any non-success state
    const finalState = (await stateBadge.textContent())?.trim() ?? '';
    expect(finalState, `final compile state: ${finalState}`).toMatch(/^Success$/i);
  });

  test('live device logs stream from cyd-office-info', async ({ page }) => {
    test.setTimeout(DEVICE_LOG_TIMEOUT_MS + 60_000);

    await page.goto('/');
    const targetRow = await findTargetRow(page);
    await expect(targetRow).toBeVisible({ timeout: 30_000 });

    // Open the row's hamburger menu
    const menuTrigger = targetRow.locator('.action-menu-trigger');
    await expect(menuTrigger).toBeVisible();
    await menuTrigger.click();

    // Click "Live Logs"
    await page.getByRole('button', { name: /^live logs$/i }).click();

    // The DeviceLogModal also uses xterm — wait for it to render
    const terminal = page.locator('.xterm-screen').first();
    await expect(terminal).toBeVisible({ timeout: 10_000 });

    // Wait for at least some content to stream from the device
    await expect.poll(
      async () => (await terminal.textContent())?.length ?? 0,
      { timeout: DEVICE_LOG_TIMEOUT_MS, message: 'expected device log lines to stream' },
    ).toBeGreaterThan(20);

    // Close it
    await page.keyboard.press('Escape');
  });
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Find the device row for TARGET_FILENAME.
 *
 * The device cell renders both the friendly name and the filename stem in
 * a `.device-filename` element. The filename stem is unique even when many
 * devices share a friendly name pattern.
 */
async function findTargetRow(page: Page) {
  // Wait for the table to have rendered any rows at all
  const anyRow = page.locator('table tbody tr').first();
  await expect(anyRow).toBeVisible({ timeout: 30_000 });

  return page.locator('table tbody tr')
    .filter({ has: page.locator('.device-filename', { hasText: TARGET_STEM }) })
    .first();
}

/**
 * Find the queue row for our TARGET_FILENAME, restricted to non-terminal
 * states first so we don't accidentally pick up an old finished job.
 *
 * Falls back to any matching row if no in-progress one exists yet.
 */
async function findQueueRow(page: Page) {
  return page.locator('table tbody tr')
    .filter({ has: page.locator('.device-filename', { hasText: TARGET_STEM }) })
    .first();
}
