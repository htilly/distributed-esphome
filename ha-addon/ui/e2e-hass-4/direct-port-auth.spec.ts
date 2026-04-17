import { expect, test } from '@playwright/test';

/**
 * #82 — direct-port auth covers the static UI shell, not just /ui/api/*.
 *
 * Before the fix, `require_ha_auth=true` (mandatory since AU.7 in 1.5.0)
 * gated JSON API calls with 401 but served the React SPA HTML and Vite
 * JS bundle to anyone on the LAN — an attacker could version-fingerprint
 * the add-on and enumerate the API surface without authenticating.
 *
 * This test asserts the fix: every protected UI path on the direct port
 * returns 401 without a Bearer token, and returns 200 when we send the
 * add-on's shared system token.
 *
 * FLEET_TOKEN is the same token the "Connect Worker" modal shows — the
 * add-on `api_token` option. Required for the 200-path assertions.
 */

const FLEET_URL = (process.env.HASS4_URL || 'http://hass-4.local:8765').replace(/\/$/, '');
const FLEET_TOKEN = process.env.HASS4_FLEET_TOKEN || '';

const PROTECTED_PATHS = ['/', '/index.html', '/ui/api/info'] as const;

test.describe('#82 direct-port auth covers the SPA shell', () => {
  for (const path of PROTECTED_PATHS) {
    test(`${path} without auth returns 401`, async ({ request }) => {
      const resp = await request.get(`${FLEET_URL}${path}`);
      expect(
        resp.status(),
        `GET ${path} without Bearer should 401 under require_ha_auth=true`,
      ).toBe(401);
      expect(
        resp.headers()['www-authenticate'] || '',
        'WWW-Authenticate header should advertise Bearer realm',
      ).toMatch(/^Bearer/);
    });
  }

  test('/ without auth does NOT leak HTML content', async ({ request }) => {
    // Defensive: even if the status were wrong, the body must not contain
    // the React shell's <!DOCTYPE> / <div id="root"> / bundle script tags.
    const resp = await request.get(`${FLEET_URL}/`);
    const body = await resp.text();
    expect(body.toLowerCase()).not.toContain('<!doctype');
    expect(body).not.toContain('id="root"');
  });

  test.describe('with a valid system Bearer', () => {
    test.skip(
      !FLEET_TOKEN,
      'HASS4_FLEET_TOKEN not set — export the add-on api_token to run the 200-path checks.',
    );

    for (const path of PROTECTED_PATHS) {
      test(`${path} with Bearer returns 200`, async ({ request }) => {
        const resp = await request.get(`${FLEET_URL}${path}`, {
          headers: { Authorization: `Bearer ${FLEET_TOKEN}` },
        });
        expect(
          resp.status(),
          `GET ${path} with valid Bearer should succeed (got ${resp.status()})`,
        ).toBe(200);
      });
    }

    test('/ with Bearer serves the React SPA shell', async ({ request }) => {
      const resp = await request.get(`${FLEET_URL}/`, {
        headers: { Authorization: `Bearer ${FLEET_TOKEN}` },
      });
      expect(resp.status()).toBe(200);
      const body = await resp.text();
      expect(body.toLowerCase()).toContain('<!doctype');
      expect(body).toContain('id="root"');
    });
  });
});
