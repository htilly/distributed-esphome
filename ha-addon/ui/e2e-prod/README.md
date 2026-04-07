# Production Smoke Tests

End-to-end smoke tests that run against a **real** distributed-esphome
instance (e.g. hass-4) and exercise the full compile + OTA path against a
real ESP device.

These are intentionally **separate** from the mocked Playwright tests in
`../e2e/`:

- The mocked tests run in CI on every push, with API responses stubbed
  via `page.route()`. They're fast and verify UI behavior in isolation.
- These prod tests touch real state — they enqueue real compile jobs,
  flash real firmware, and tail real device logs. They are not run in CI.

## Prerequisites

- A running distributed-esphome instance reachable from your machine
  (default: `http://192.168.225.112:8765`)
- The target device must exist in the configured ESPHome dir
  (default: `cyd-office-info.yaml`)
- The target device should be online so OTA succeeds (otherwise the
  test will correctly fail)

## Running

```bash
cd ha-addon/ui

# defaults: PROD_URL=http://192.168.225.112:8765, PROD_TARGET=cyd-office-info.yaml
npm run test:e2e:prod

# override target device
PROD_TARGET=living-room.yaml npm run test:e2e:prod

# override server URL (e.g. running locally on a different host)
PROD_URL=http://192.168.1.42:8765 npm run test:e2e:prod

# headed mode (watch the browser)
PROD_URL=http://192.168.225.112:8765 \
  npx playwright test --config=e2e-prod/playwright.config.ts --headed
```

## Configuration

| Env var            | Default                       | Description                                       |
|--------------------|-------------------------------|---------------------------------------------------|
| `PROD_URL`         | `http://192.168.225.112:8765`          | Base URL of the running add-on (NOT the HA Ingress URL — talk to the add-on directly) |
| `PROD_TARGET`      | `cyd-office-info.yaml`        | Filename of the target ESPHome config             |
| `COMPILE_BUDGET_MS`| `480000` (8 minutes)          | Max time to wait for compile + OTA to complete    |
| `EXPECTED_VERSION` | contents of `ha-addon/VERSION`| Add-on version the suite expects on the server. The first test fails fast if `/ui/api/server-info` returns a different version, preventing accidental tests against a stale deploy. |

## Version safety check

Before any other test runs, the suite reads `ha-addon/VERSION` from the
working tree and asserts that the running add-on reports the same version
via `/ui/api/server-info`. This prevents accidentally testing against a
stale deploy after a `git pull`. If the deploy is out of date, run
`./push-to-hass-4.sh` first.

## Test Cases

The test file `cyd-office-info.spec.ts` runs four sequential cases:

1. **Devices tab loads** — header renders, target device row is visible
2. **Schedule upgrade** — click the row's Upgrade button, switch to Queue,
   verify the job appears
3. **Compile + log tail** — open the log modal, verify lines stream into
   the xterm terminal, then poll the queue row state until it reaches
   Success (or fail the test if it ends in any other terminal state)
4. **Live device logs** — open the row's hamburger menu, click Live Logs,
   verify the device API streams output into the modal

Tests run **serially** (`workers: 1`, `fullyParallel: false`) because they
share global state on the real server.

## Why not use HA Ingress?

The add-on exposes port 8765 directly to the host network in addition to
being available through HA Ingress. The `/ui/api/*` endpoints don't require
authentication when accessed directly (this is documented in
`dev-plans/SECURITY_AUDIT.md` finding F-03).

For production smoke tests, talking to the add-on port directly is the
simplest approach: no HA login flow, no Ingress path discovery, no token
juggling. If you want to test the Ingress path itself, you'd need to set
up HA long-lived access tokens and navigate through the HA frontend.
