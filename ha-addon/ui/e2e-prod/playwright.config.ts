import { defineConfig } from '@playwright/test';

/**
 * Playwright config for production smoke tests against a real running
 * distributed-esphome instance (e.g. hass-4).
 *
 * Run with:
 *   npm run test:e2e:prod
 *
 * Defaults to http://192.168.225.112:8765 (hass-4). Override with:
 *   PROD_URL=http://other-host:8765 npm run test:e2e:prod
 */
export default defineConfig({
  testDir: '.',
  // Compile + OTA can take a while on real hardware
  timeout: 10 * 60_000,
  expect: { timeout: 30_000 },
  retries: 0,
  // Run prod tests serially — they touch real state, so don't parallelize
  workers: 1,
  fullyParallel: false,
  reporter: [['list'], ['html', { outputFolder: 'playwright-report-prod', open: 'never' }]],
  use: {
    baseURL: process.env.PROD_URL || 'http://192.168.225.112:8765',
    headless: true,
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    trace: 'retain-on-failure',
    // Real network may be slower than localhost
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
  },
});
