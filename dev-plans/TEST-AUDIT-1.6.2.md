# Test Audit — 1.6.2

**Authored:** 2026-04-24 against `develop` at `5429684b28d91db10ba0bb18f42fa99b63197fb4` (PR #87).
**Scope:** identify the biggest coverage gaps across the Python + Playwright + CI surfaces as of the 1.6.2 release, and rank areas most likely to regress in 1.6.3+.

Not a pat-on-the-back doc. The suite grew substantially this cycle — 43 Python test files (~810 test functions), 24 mocked Playwright specs (128 tests), 6 real-hass prod-smoke specs, two new real-flow integration test files, and three new CI workflows. Several 1.6.1 blind spots are now closed. But three new surfaces landed in 1.6.2 with partial or zero unit coverage, and the biggest single gap (IT.2) carried forward unchanged.

---

## Coverage landscape (snapshot)

| Surface | Files | Tests | Notes |
|---|---|---|---|
| `tests/test_*.py` (unit + service) | 43 | ~810 | Ruff/mypy/pytest gate CI on every push. Big modules: `test_ui_api.py` (69 tests, 1503 LOC), `test_queue.py` (64 tests, 1099 LOC), `test_git_versioning.py` (48 tests, 952 LOC), `test_settings.py` (50 tests). |
| `tests/test_integration_*_logic.py` | 10 | 78 | All SimpleNamespace / mock-based. Cheap to run; don't catch the CR.12-class lifecycle bugs they're named for. |
| `tests/test_integration_setup.py` | 1 | 3 | Imports `pytest_homeassistant_custom_component`. **No longer skipped** — 3 real-hass tests run (happy path, recovery after first-poll failure, reload cycle). IT.2 is partially closed; lifecycle teardown depth is still shallow (see blind spot #1). |
| `tests/test_integration_reauth_flow.py` | 1 | 4 | **New in 1.6.2 (TR.6).** Real-hass tests for `async_step_reauth` / `async_step_reauth_confirm` — closes the PR #80 review findings about missing entry-ID safety. |
| `tests/test_integration_reconfigure_flow.py` | 1 | 4 | **New in 1.6.2 (TR.6).** Real-hass tests for `async_step_reconfigure`. Closes the dead-code fallback + KeyError findings from PR #80 review. |
| `ha-addon/ui/e2e/*.spec.ts` (mocked) | 24 | 128 | Fast (~1–2 min). Good UI regression net for rendered state + route interception. |
| `ha-addon/ui/e2e-hass-4/*.spec.ts` (real) | 6 | ~20 | Full compile + OTA against a real device (`cyd-office-info`). The only surface that exercises the worker → ESP32 path end-to-end. |
| `.github/workflows/hassfest.yml` | 1 | (opaque) | Now validates `quality_scale.yaml` rule-by-rule — `quality_scale.yaml` committed in 1.6.2-dev.38. Hassfest reads per-rule status and fails on `status: done` claims the code doesn't satisfy. |
| `.github/workflows/compile-test.yml` | 1 | 16 fixtures | Real `esphome compile` across 16 YAMLs, pinned to `2026.4.2` (was `2026.3.2`). Single-version; still no multi-version matrix. |
| `.github/workflows/build.yml` | 1 | 3 builds | **New in 1.6.2 (CI.1).** `docker buildx build` on PR + push for client, server-addon, and server-standalone Dockerfiles. Pre-merge catch for Dockerfile breakage. |
| `.github/workflows/apparmor.yml` | 1 | 2 jobs | **New in 1.6.2 (CI.2).** Profile syntax check (`apparmor_parser -N`) + full container launch-under-profile liveness probe. |
| `scripts/check-invariants.sh` | 1 | 16 rules | Grep-shaped: PY-1..10, UI-1..7, E2E-1. Cheap and reliable; limited to patterns that grep can see. |

---

## Top blind spots, ranked by (likelihood × blast radius)

### 1. HA integration lifecycle — IT.2 partially closed, teardown depth thin (HIGH / HIGH)

`tests/test_integration_setup.py` now has 3 real-hass tests (previously 2 skipped). The reload cycle test (`test_async_setup_then_reload`) proves setup → unload → setup doesn't crash. What it doesn't assert: listeners are fully removed on unload (no double-fire), service registrations don't duplicate after reload, `async_on_unload` callbacks actually fire. The CR.12 shape (listener leak or service-registered-twice) would survive the current reload test if it only triggers on the second call of a specific platform's `async_setup_entry`.

The 10 `_logic.py` suites (~78 tests) pass against `SimpleNamespace` mocks and remain unable to catch lifecycle bugs. PY-10 invariant is now load-bearing — the real test runs.

**Fix direction** — extend `test_integration_setup.py` with: (a) a reload test that asserts the event-listener count before and after is identical (not just "no crash"), and (b) a test that verifies no duplicate service registrations after reload. These are the two specific forms that have historically shipped unnoticed.

**Severity:** the class-of-bug from 1.5 (CR.12) is still reachable. One new entity platform added without matching `async_on_unload` wire-up would not be caught.

### 2. Diagnostics endpoints have no unit-test coverage (MED / HIGH)

`tests/test_diagnostics.py` has 7 tests covering the `DiagnosticsBroker` and the helper functions (`in_process_thread_dump`, `run_self_thread_dump`, `run_self_thread_dump_async`). But the three new `ui_api.py` endpoints are untested at the HTTP layer:

- `POST /ui/api/diagnostics/server` — server self-dump returned as a streaming download.
- `POST /ui/api/workers/{id}/request-diagnostics` — mints a broker request and returns the `request_id`.
- `GET /ui/api/workers/{id}/diagnostics/{request_id}` — polls the broker for the worker's result and streams it.

There is mocked Playwright coverage for the UI round-trip (`worker-actions.spec.ts:113` and `:148`), which verifies the frontend fires the routes and handles the responses correctly. But the server-side request handling is live-verified only — no `test_ui_api.py` assertions for: 404 on unknown worker ID, 404/202 on unresolved `request_id`, broker-absent path, or streaming headers on success.

The worker-side `_in_process_thread_dump()` in `ha-addon/client/client.py` is a duplicate of the server's helper but is untested (the test file only covers `ha-addon/server/diagnostics.py`). A divergence between the two implementations would not be caught.

**Fix direction** — add 4–5 tests in `test_ui_api.py`: server self-dump 200 + `X-Diagnostics-Ok: true` header; request-worker-diagnostics 200 + returns `{request_id}`; request-diagnostics on unknown worker 404; get-diagnostics pending 202; get-diagnostics resolved 200. The worker-side `_in_process_thread_dump` can share test logic or at minimum have a module-level assertion that its output matches the server's version for a synthetic stack.

### 3. `_ensure_config_dir` (ui_api.py #190) and the config-dir existence gate (main.py #191) have no test coverage (MED / MED)

Both landed in 1.6.2 to close fresh-install failures on truly-empty installs:

- `_ensure_config_dir` (`ui_api.py:2155`) lazy-creates `/config/esphome` on the first UI write; calls back into `init_repo` when versioning is on.
- The `#191` gate in `main.py` passes `fresh_repo=None` instead of calling `init_repo` when the config dir doesn't exist at boot.

Neither path appears in `test_ui_api.py` or `test_main.py`. The happy-path (dir already exists) is exercised implicitly by every existing test. The "dir is absent" branch — the one that actually matters for the bug class being fixed — is only verified by a real fresh-install deploy (which is exactly the scenario that caused the original bugs).

**Fix direction** — `test_ui_api.py`: one parametrized test for `_ensure_config_dir` covering (existing dir = no-op) and (absent dir = mkdir + logs "Created"). `test_main.py`: a test asserting that when the config dir path doesn't exist, `init_settings` receives `fresh_repo_init=None` rather than `False` (the pre-fix shape).

### 4. `_resolve_esphome_config` returning `None` class-of-bug — still deferred (HIGH / HIGH)

Unchanged from 1.6.1. The reseed hook (`reseed_device_poller_from_config`) has 3 narrow tests in `test_main.py`. No fixture suite enumerates the address-source paths (`wifi_use_address`, `wifi_static_ip`, `ethernet_static_ip`, `wifi_static_ip_via_substitution`, etc.) and asserts each resolves to the correct OTA target. No invariant in `check-invariants.sh` catches a future consumer of `_address_overrides` / `_encryption_keys` that forgets the reseed wire-up.

Bugs #11 and #18 (1.6.1) were different symptoms of this same root cause. The class is still open.

**Fix direction** — unchanged from 1.6.1 audit: (a) `check-invariants.sh` rule flagging new readers of `_address_overrides`/`_encryption_keys` that aren't wired into `reseed_device_poller_from_config`; (b) fixture-driven suite for each address-source path using ESPHome's `CORE.address` as oracle.

### 5. `ha-addon/server/mdns_advertiser.py` still has no dedicated test file (MED / MED)

`tests/test_mdns_advertiser.py` does not exist. The module was new in 1.6.1; nothing changed in 1.6.2 to add coverage. The known failure modes (see 1.6.1 audit item #5) remain untested: `_primary_ipv4()` returning `None`, `socket.gethostname()` returning `"localhost"`, stop-before-start race.

**Fix direction** — add `tests/test_mdns_advertiser.py` with happy-path register/unregister (mock `AsyncZeroconf`), `_primary_ipv4 is None` branch, and `stop()` before `start()` doesn't crash. ~3 tests.

### 6. `compile-test.yml` pinned to single ESPHome version (LOW / MED)

Now pinned at `2026.4.2` (up from `2026.3.2`). Still a single version; no matrix on `{pinned, latest_stable}`. If ESPHome ships a compile-time regression in `2026.5+`, it's invisible to CI until a user reports it.

**Fix direction** — unchanged from 1.6.1: matrix on `{pinned, latest_stable}` adds ~6–8 min in parallel per push and provides early warning of upstream regressions.

### 7. Git-versioning races aren't stress-tested (MED / HIGH)

Unchanged from 1.6.1. `tests/test_git_versioning.py` is 48 tests of strong functional coverage; the `.git/index.lock` race and concurrent `commit_file` calls are not exercised.

**Fix direction** — unchanged: one stress test with 50 concurrent `asyncio.gather` commits; assert 50 entries in `git log`, no lock error.

### 8. `firmware_storage.py` coverage thin relative to surface (MED / MED)

Unchanged from 1.6.1. Budget enforcer + orphan reconciler under concurrent write pressure are untested.

**Fix direction** — unchanged: one stress test with 10 concurrent uploads; assert budget eviction picks the right victim.

### 9. Protocol cross-version compat (LOW / HIGH)

Unchanged from 1.6.1. `WorkerDiagnosticsUpload` and the new `diagnostics_request_id` heartbeat field are new wire fields. The byte-identical-files invariant (PY-6) is still the only guard; no test simulates a worker at protocol version N connecting to a server at N+1.

**Fix direction** — unchanged: pin an old `protocol.py` under `tests/fixtures/` and test graceful `ProtocolError-with-mismatch` rather than undefined-field crashes.

### 10. Worker-selection reason edge cases (LOW / LOW)

Unchanged from 1.6.1. Tie-breaker and racing-heartbeat scenarios are still not covered.

---

## New 1.6.2 blind spots (surfaces with zero prior coverage)

### N1. `test-matrix.py` orchestrator has no unit tests (LOW / LOW — intentional non-goal)

`scripts/test-matrix.py` is ~500 LOC of dev-loop glue: image builds, parallel deploys, Playwright runs, result collation. It has no test file. This is a conscious non-goal — the script is only run by developers and its "tests" are the Playwright suites it invokes. Failures surface as explicit error output. Flagged here for completeness, not as a gap that needs closing.

### N2. `scripts/standalone/` deploy scripts have no CI test path (LOW / LOW)

The four scripts added in HT.14 (`bootstrap-esphome.sh`, `deploy.sh`, `seed-fleet.sh`, `teardown.sh`) are only exercised by the `standalone-pve` target in `test-matrix.py`. There is no unit/mock test. This is acceptable for shell script glue; the real-path smoke via `test-matrix.py` is the intended gate.

### N3. Worker-side `_in_process_thread_dump` divergence risk (MED / LOW)

`ha-addon/client/client.py:508` contains a copy of `in_process_thread_dump()` from `ha-addon/server/diagnostics.py`. The byte-identical-files invariant (PY-6) covers `protocol.py` but not these helper copies. If the two implementations diverge, neither the protocol test nor any other automated check catches it.

**Fix direction** — either extract `in_process_thread_dump` into a shared module (cleanest), or add a test that imports both and asserts their output on a synthetic stack is structurally equivalent.

---

## Most-likely-to-regress areas for 1.6.3 (forward-looking)

Prioritised by recency × complexity × test-coverage-gap:

1. **Diagnostics round-trip** (#2 above). New protocol fields + new UI endpoints with no HTTP-layer tests. First place a 1.6.3 regression would hide.
2. **Fresh-install path** (#3 above). `_ensure_config_dir` and the `#191` main.py gate close two real bugs; no regression guard exists. The next person who touches `main.py`'s boot sequence could re-introduce either shape silently.
3. **HA integration lifecycle teardown** (#1 above). New entity platforms or services added in 1.6.3 land with the same CR.12 risk as before the real-hass tests — because the tests prove "no crash on reload" not "no listener leak."
4. **`_resolve_esphome_config` / reseed chain** (#4 above). Any new consumer of the three address-source maps in 1.6.3 is a potential bug #3.
5. **AppArmor narrowing** (SS.1 deferred). The `apparmor.yml` workflow now proves syntax + liveness. Any move from permissive to narrow rules is safe to iterate on CI — the workflow is the feedback loop the 1.6.1 audit asked for. Risk is now low if SS.1 uses the CI gate as intended.
6. **ESPHome `2026.5+` breaking change**. CI pins `2026.4.2`; multi-version matrix is the remaining gap (#6 above).

---

## Mechanical test-tooling gaps

| Gap | Impact | Fix |
|---|---|---|
| No diagnostics HTTP-layer tests | New endpoints live-verified only | 4–5 tests in `test_ui_api.py` (#2 above) |
| `_ensure_config_dir` / `#191` gate untested | Fresh-install regression has no guard | 2 targeted tests in `test_ui_api.py` / `test_main.py` (#3) |
| No `test_mdns_advertiser.py` | New module at 0% unit coverage | Add file (~3 tests) (#5) |
| Worker + server `_in_process_thread_dump` duplication | Silent divergence possible | Extract to shared module or divergence-detection test (#N3) |
| `compile-test.yml` single ESPHome version | Upstream regressions land in prod | Matrix on {pinned, latest_stable} (#6) |
| No reseed-consumer invariant | Class-of-bug #4 can re-ship | New `check-invariants.sh` rule (#4) |
| No protocol-version mismatch test | PY-6 byte-check is the only guard | Pinned-old-fixture test (#9) |
| No stress tests anywhere | Race conditions surface in prod | `test_stress.py` with git-commit + firmware-upload scenarios (#7, #8) |

---

## Recommended 1.6.3 test-work, in order

1. **Diagnostics HTTP tests** — 4–5 tests in `test_ui_api.py`; closes the biggest new gap from 1.6.2.
2. **`_ensure_config_dir` + `#191` regression guards** — 2 tests; cheap, covers a real-user-visible fresh-install path.
3. **IT.2 teardown depth** — extend `test_integration_setup.py` to assert listener count + no service duplication after reload; closes the remaining CR.12 risk.
4. **Worker `_in_process_thread_dump` dedup** — extract or add divergence-detection test; cheap, closes N3.
5. **`test_mdns_advertiser.py`** — ~3 tests; module has been unprotected since 1.6.1.
6. **Reseed-consumer invariant** (`check-invariants.sh`) — closes the #11/#18 bug class durably.
7. **Multi-version `compile-test.yml` matrix** — upstream regression early warning.
8. **Static-IP fixture suite** — the deferred-in-#18 work; where the next address-resolution regression lives.
9. **One stress test** (git commit races or firmware upload races) — build the pattern.
10. **Protocol mismatch fixture** — one test; catches the next PY-6 slip.

---

## Archive — blind spots FIXED in 1.6.2

These appeared in the 1.6.1 audit. They are closed and listed here for historical context only.

- **#2 Silver-tier rules unenforced** — **FIXED in 1.6.2 via TP.3(a).** `quality_scale.yaml` committed; `hassfest.yml` now validates the claim rule-by-rule on every PR.
- **#3 AppArmor profile has zero regression surface** — **FIXED in 1.6.2 via CI.2.** `apparmor.yml` workflow lands syntax check + container launch-under-profile on every push.
- **#7 Dockerfile + AppArmor integration has no pre-push guard** — **FIXED in 1.6.2 via CI.1.** `build.yml` runs `docker buildx build` on PR for all three Dockerfiles.
- **#12 Connect Worker docker-run bash branch missing `--network host`** — **FIXED in 1.6.2 via TR.4 (dev.28).** Regression test in `ha-addon/ui/e2e/worker-actions.spec.ts:157` asserts both docker and PowerShell branches include `--network host`.
- **#9 (partial) `async_step_reconfigure` has logic tests but no flow test** — **FIXED in 1.6.2 via TR.6.** `test_integration_reconfigure_flow.py` and `test_integration_reauth_flow.py` land 4 real-hass tests each.

---

## Non-goals

- Coverage percentages. The existing suite is substantial; adding uniform coverage to every module dilutes attention from the class-of-bug shapes above. Aim for *quality* of coverage (does the test catch the bug it's named after?) not quantity.
- "Comprehensive" integration tests that try to exercise every HA platform. Start with setup/unload/reload; extend as specific lifecycle bugs surface.
- Real-hass `e2e-hass-4/` growth beyond ~20 tests. The suite's value is end-to-end smoke, not exhaustive coverage — every added test here adds minutes to every push. Cap + prune.
- Unit-testing dev-loop shell scripts (`test-matrix.py`, `scripts/standalone/`). The real-path smoke via `test-matrix.py` is the intended gate for these.
