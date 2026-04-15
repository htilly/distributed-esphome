import { expect, test } from '@playwright/test';
import { mockApi } from './fixtures';

// PW.7 — Theme + responsive Playwright tests

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

// ---------------------------------------------------------------------------
// Theme toggle + persistence
// ---------------------------------------------------------------------------

test('theme toggle switches data-theme attribute', async ({ page }) => {
  await page.goto('/');
  const html = page.locator('html');

  // Default is dark — no data-theme attribute set
  await expect(html).not.toHaveAttribute('data-theme', 'light');

  const toggle = page.locator('header button[title*="Switch to"]');
  await toggle.click();
  await expect(html).toHaveAttribute('data-theme', 'light');

  await toggle.click();
  await expect(html).not.toHaveAttribute('data-theme', 'light');
});

test('theme preference persists across reloads', async ({ page }) => {
  await page.goto('/');
  // Switch to light
  await page.locator('header button[title*="Switch to"]').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

  // Reload and confirm light mode is restored from localStorage
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

  // Switch back so we don't leave the test browser in a non-default state
  await page.locator('header button[title*="Switch to"]').click();
});

test('streamer mode toggle adds .streamer class to html', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('Living Room Sensor')).toBeVisible({ timeout: 5000 });

  const html = page.locator('html');
  // The streamer toggle is the only header button whose title mentions "streamer mode"
  const streamerToggle = page.locator('header button[title*="streamer mode" i]');
  await streamerToggle.click();
  await expect(html).toHaveClass(/streamer/);

  // Toggle off
  await streamerToggle.click();
  await expect(html).not.toHaveClass(/streamer/);
});

// ---------------------------------------------------------------------------
// Viewport responsiveness
// ---------------------------------------------------------------------------

test('narrow viewport: tabs and header still rendered', async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 800 });
  await page.goto('/');

  await expect(page.locator('header')).toBeVisible();
  await expect(page.getByRole('button', { name: /Devices/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Queue/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Workers/ })).toBeVisible();
});

test('narrow viewport: window-level horizontal scroll is locked', async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 800 });
  await page.goto('/');
  await expect(page.getByText('Living Room Sensor')).toBeVisible({ timeout: 5000 });

  // The window itself shouldn't scroll horizontally — that's the
  // "page yanks sideways on a phone swipe" bug. We don't assert on
  // body.scrollLeft here because Tailwind preflight + flex layouts
  // can leave body slightly scrollable even with overflow-x: hidden;
  // window.scrollX is the property the user actually feels.
  const winX = await page.evaluate(() => {
    window.scrollTo(2000, 0);
    return window.scrollX;
  });
  expect(winX).toBe(0);
});

test('narrow viewport: table-wrap is the horizontal scroll container', async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 800 });
  await page.goto('/');
  await expect(page.getByText('Living Room Sensor')).toBeVisible({ timeout: 5000 });

  // The .table-wrap div is where horizontal scroll lives for wide tables.
  // Verify its computed overflow-x is auto so it actually shows a scrollbar
  // when needed.
  const wrap = page.locator('.table-wrap').first();
  await expect(wrap).toBeVisible();
  const overflowX = await wrap.evaluate(el => getComputedStyle(el).overflowX);
  expect(overflowX).toBe('auto');
});

test('narrow viewport: header is horizontally scrollable so every control is reachable (#1)', async ({ page }) => {
  // iPhone SE width — narrow enough to overflow the header's natural width.
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto('/');
  await expect(page.getByText('Distributed Build')).toBeVisible({ timeout: 5000 });

  const header = page.locator('header');
  // overflow-x: auto turns the header into its own scroll container.
  const overflowX = await header.evaluate(el => getComputedStyle(el).overflowX);
  expect(overflowX).toBe('auto');

  // Sanity: header content is wider than viewport (i.e. there's something
  // to scroll). If this stops being true we should remove the test, not
  // tighten the assertion.
  const { scrollWidth, clientWidth } = await header.evaluate(el => ({
    scrollWidth: el.scrollWidth,
    clientWidth: el.clientWidth,
  }));
  expect(scrollWidth).toBeGreaterThan(clientWidth);

  // Streamer-mode toggle is the last interactive control before the spacer
  // and most likely to be off-screen on iOS Safari. Scroll the header to
  // bring it into view and assert it becomes reachable.
  const streamerBtn = page.locator('header button[aria-label*="streamer mode"]');
  await streamerBtn.scrollIntoViewIfNeeded();
  await expect(streamerBtn).toBeInViewport();
});

test('desktop viewport: page renders without horizontal scroll', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto('/');
  await expect(page.getByText('Living Room Sensor')).toBeVisible({ timeout: 5000 });

  const scrolled = await page.evaluate(() => {
    window.scrollTo(2000, 0);
    return window.scrollX;
  });
  expect(scrolled).toBe(0);
});
